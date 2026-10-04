"""``plan file``: each kind's filing, its checks, its board place, and the same command
finishing a filing a failure cut short (R-BAL186), over :class:`_fake.FakeTracker` and a
throwaway git repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls
GitHub.
"""
from __future__ import annotations

import pytest
import requests

from _fake import FakeTracker, run, ship
from _github import GitHubError
from _tracker import Child, Claim, Edit, OutsideLink
from check import ruling_body


# -- file ----------------------------------------------------------------------------------

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



# -- the review of checkpoint 2 ------------------------------------------------------------

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



# -- the review of checkpoint 3 ------------------------------------------------------------

def test_a_question_quoting_a_rulings_shape_keeps_every_word_as_its_ruling(code, tmp_path):
    """Review cp3 H-1: a question whose text quotes a ruling's marks was read as a conversion
    already done and cut at its first mark, losing the question it actually asks."""
    asked = ("Question: Your ruling R-BAL178 reads, word for word:\n\n"
             "Answer: \"A short name the session writes\".\n\n"
             "Should the cap be 100 characters or 200?")
    answer = tmp_path / "a"
    answer.write_text("100.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body=asked, title="Cap")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Cap",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 0
    assert tracker.bodies[2] == ruling_body(asked, "100.")
    assert tracker.cards([1])[1].children == (Child(2, "ruling", False),)



def test_an_open_ruling_given_another_answer_is_refused_unwritten(code, tmp_path, capsys):
    """Review cp3 L-h: a ruling whose close failed, taken as --from-question with a NEW
    answer, had the developer's recorded answer replaced."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "ruling", True),))
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=1)
    answer = tmp_path / "a"
    answer.write_text("There.")
    args = ("file", "ruling", "--arc", "balance", "--title", "Home", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *args) == 1
    assert "already a ruling, and not with this answer" in capsys.readouterr().err
    assert not tracker.writes
    answer.write_text("Here.")
    assert run(tracker, code, *args) == 0
    assert tracker.writes == [("close", 2, "completed")]



def test_a_step_passed_as_the_question_is_refused_unwritten(code, tmp_path, capsys):
    """Review cp3 M-5: with the kind refusal deleted, a step would be retyped a ruling, its
    spec rewritten and closed."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, body="Build the walk.", title="Walk")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Walk",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "is not an open question" in capsys.readouterr().err
    assert not tracker.writes



def test_a_conversion_takes_the_title_the_filing_names(code, tmp_path):
    """Review cp3 M-5: with the retitle deleted, a ruling kept its question's name."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight?")
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship tonight",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 0
    assert ("retitle", 2, "Ship tonight") in tracker.writes
    assert tracker.cards_by_number[2].title == "Ship tonight"



def test_a_leaf_is_never_filed_under_a_card_with_no_type(code, tmp_path, capsys):
    """Review cp3 M-4: an untyped parent passed as "no parent"; the leaf was attached and the
    untyped card taken off the board as though it were the step split."""
    spec = tmp_path / "spec.md"
    spec.write_text("Half.")
    tracker = FakeTracker()
    tracker.add(1, kind=None)
    tracker.board.items.append(1)
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "half",
               "--parent", "plan#1", "--body-file", str(spec)) == 1
    assert "its parent is a card with no type" in capsys.readouterr().err
    assert not tracker.writes



def test_a_top_level_filing_never_finishes_a_leaf(code, tmp_path, capsys):
    """Review cp3 L-g: a step filed with no parent "finished" an existing leaf of the same
    title and text, a card that filing could never have made."""
    spec = tmp_path / "spec.md"
    spec.write_text("New work.")
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, body="New work.", title="New", parent=1)
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "New",
               "--body-file", str(spec)) == 1
    err = capsys.readouterr().err
    assert "a sub-issue of plan#1, not a top-level card" in err
    assert not tracker.writes



def test_a_card_filed_is_said_before_its_read_back(code, tmp_path, capsys, monkeypatch):
    """Review cp3 L-b: when the read-back after ``create`` failed, nothing said the card
    existed."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()

    def unreachable(_numbers):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(tracker, "cards", unreachable)
    assert run(tracker, code, "file", "question", "--arc", "recurrence", "--title", "Day",
               "--body-file", str(text)) == 2
    assert tracker.writes[0][0] == "create"
    assert capsys.readouterr().out.startswith("filed plan#1\n")



def test_an_open_card_of_another_kind_is_never_finished_as_this_filing(code, tmp_path, capsys):
    """Review cp3 M-5: with the kind match deleted, a step filing "finished" a question of
    the same title and text."""
    text = tmp_path / "q.md"
    text.write_text("Which day?")
    tracker = FakeTracker()
    tracker.add(1, "question", body="Which day?", title="Day")
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "Day",
               "--body-file", str(text)) == 0
    assert capsys.readouterr().out.startswith("filed plan#2\n")



def test_a_leaf_already_placed_is_left_where_the_developer_moved_it(code, tmp_path, capsys):
    """Review cp3 M-5: with the early return deleted, the retry of a leaf's filing moved it
    back after its siblings, undoing the developer's drag."""
    spec = tmp_path / "spec.md"
    spec.write_text("Half.")
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    args = ("file", "step", "--arc", "balance", "--parent", "plan#2", "--body-file", str(spec))
    assert run(tracker, code, *args, "--title", "first half") == 0
    assert run(tracker, code, *args, "--title", "second half") == 0
    assert tracker.board.items == [1, 4, 5, 3]
    assert run(tracker, code, "move", "plan#5", "--top") == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args, "--title", "second half") == 0
    assert tracker.writes == writes and tracker.board.items == [5, 1, 4, 3]
    assert "board: already on it" in capsys.readouterr().out


def test_a_conversion_retried_with_another_answer_waits_until_its_question_is_back(code,
                                                                                    tmp_path,
                                                                                    capsys):
    """Review cp4 M-2: the body landed and the retype failed; the developer corrected his
    answer and the same filing ran again, and the tool's own earlier body was wrapped as
    the question ("Question: Question: ... Answer: Yes. Answer: No.").  A body the tool's
    edit saved in a ruling's shape with another answer is refused until the developer's
    question is put back."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    _FailOnce(tracker, "retype")
    args = ("file", "ruling", "--arc", "balance", "--title", "Tonight", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *args) == 2
    answer.write_text("No, tomorrow.")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "text is an earlier conversion's" in capsys.readouterr().err
    assert tracker.writes == writes
    assert run(tracker, code, "spec-revert", "plan#2", "--to", "E2.0") == 0
    assert run(tracker, code, *args) == 0
    assert tracker.bodies[2] == ruling_body("Ship it tonight?", "No, tomorrow.")
    assert (tracker.cards_by_number[2].kind, tracker.cards_by_number[2].is_open) == ("ruling",
                                                                                     False)


def test_a_conversion_retried_over_a_question_saved_with_crlf_writes_its_body_once(code,
                                                                                   tmp_path):
    """Review cp4 M34: a question typed on the web may hold CRLF; the retry compares the body
    it wrote with the one it would write as the rules read them, so it writes it once."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it\r\ntonight?", title="Tonight")
    _FailOnce(tracker, "retype")
    args = ("file", "ruling", "--arc", "balance", "--title", "Tonight", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *args) == 2
    assert run(tracker, code, *args) == 0
    assert [write[0] for write in tracker.writes].count("set_body") == 1


def test_a_question_under_an_issue_outside_the_tracker_is_refused_before_any_write(
        code, tmp_path, capsys):
    """Review cp4 L-7: the re-homing refusal read only a parent inside the tracker, so the
    body, type and title were written before GitHub refused the second parent."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it?", title="Ship",
                outside=(OutsideLink("parent", "saltyreformed-labs/Shekel#5"),))
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 1
    assert "a sub-issue of saltyreformed-labs/Shekel#5, outside the tracker" in (
        capsys.readouterr().err)
    assert not tracker.writes


def test_a_leaf_whose_split_step_is_still_on_the_board_is_finished_into_its_place(code,
                                                                                   tmp_path):
    """Review cp4 M57: a filing cut short after the leaf joined the board but before the split
    step left it; the retry must still put the leaf in its place and take the step off."""
    spec = tmp_path / "spec.md"
    spec.write_text("Half.")
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    _FailOnce(tracker.board, "remove")
    args = ("file", "step", "--arc", "balance", "--title", "half", "--parent", "plan#2",
            "--body-file", str(spec))
    assert run(tracker, code, *args) == 2
    assert tracker.board.items == [1, 2, 4, 3]
    assert run(tracker, code, *args) == 0
    assert tracker.board.items == [1, 4, 3]


def test_the_fake_tracker_fails_loudly_on_a_re_parent_outside_the_tracker():
    """The fake's own promise (``_fake``): it never guesses GitHub's answer to a write the
    tool must never send, a second parent outside the tracker included."""
    tracker = FakeTracker()
    tracker.add(1)
    card = tracker.add(2, "question", outside=(OutsideLink("parent", "o/code#5"),))
    with pytest.raises(AssertionError, match="outside the tracker"):
        tracker.add_child(1, card)


def test_a_conversion_a_person_touched_up_is_still_refused(code, tmp_path, capsys):
    """Review cp4b L9: the refusal read only who saved last, so the tool's ruling-shaped body,
    touched up on the web, was taken as the developer's question and wrapped twice."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it tonight?", title="Tonight")
    _FailOnce(tracker, "retype")
    args = ("file", "ruling", "--arc", "balance", "--title", "Tonight", "--owner", "plan#1",
            "--from-question", "plan#2", "--answer-file", str(answer))
    assert run(tracker, code, *args) == 2
    touched = ruling_body("Ship it tonight!", "Yes.")
    tracker.versions[2].append(Edit("E2.9", "2026-10-04T00:00:09Z", "SaltyReformed", touched))
    tracker.bodies[2] = touched
    answer.write_text("No, tomorrow.")
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "text is an earlier conversion's" in capsys.readouterr().err


def test_a_question_blocked_from_outside_the_tracker_still_becomes_its_ruling(code, tmp_path):
    """Review cp4b P13: only an outside PARENT re-homes a card; an outside blocker does not."""
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "question", body="Ship it?", title="Ship",
                outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship",
               "--owner", "plan#1", "--from-question", "plan#2",
               "--answer-file", str(answer)) == 0
    assert tracker.cards_by_number[2].kind == "ruling"
