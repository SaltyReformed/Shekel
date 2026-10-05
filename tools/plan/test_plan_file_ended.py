"""Filings that ended, X-cx L2's leaf C (applications of ruling ``balance:R-BAL207``, recorded
beside R-BAL202..R-BAL206): ``plan file`` run again writes nothing over a filing an earlier
run made -- a card still marked is found in any state, and a ruling among its owner's
sub-issues -- and never files over one a decision ended; and a leaf closed while still
marked is no leaf of its split step, unless git says it shipped (``_state.leaves``).  Over
:class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture,
``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

from _fake import (AnswerLostOnce, FailOnce, FakeTracker, leaf_filing, ruling_filing, run,
                   ship)
from _github import GitHubError
from _tracker import Child
from check import ruling_body
from setup_tracker import FILING


def _closed_by_hand(tracker, number, reason="COMPLETED"):
    """A person closes plan#``number`` on the web (GitHub's default reason: completed)."""
    tracker.cards_by_number[number] = replace(
        tracker.cards_by_number[number], is_open=False, state_reason=reason,
        closed_by_tool=False, touched_by_hand=True)


def _rulings(tracker):
    """Every ruling card: (number, parent, open, marked)."""
    return [(card.number, card.parent, card.is_open, FILING in card.labels)
            for card in tracker.cards_by_number.values() if card.kind == "ruling"]


# -- (a) a ruling's filing run again ------------------------------------------------------

def test_a_finished_rulings_command_run_again_writes_nothing(code, tmp_path, capsys):
    """Review rbal202b M1 probe 3: a finished ruling is closed and unmarked, so neither the
    open listing nor the marked one holds it, and the plain repeat filed a second ruling;
    it is found among its owner's sub-issues."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes
    assert capsys.readouterr().out.startswith("plan#2 [ruling, balance] Home is filed already")
    assert _rulings(tracker) == [(2, 1, False, False)]


def test_a_ruling_whose_unmark_landed_but_lost_its_answer_is_not_filed_again(code, tmp_path):
    """Review rbal202b M1 probe 1b, rbal202b2 LOW 5: the unmark lands and its answer is lost
    (exit 2); the documented retry filed a second ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    AnswerLostOnce(tracker, "unmark")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    assert _rulings(tracker) == [(2, 1, False, False)]
    writes = list(tracker.writes)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, 1, False, False)]


def test_a_ruling_whose_mark_was_removed_mid_filing_is_not_filed_again(code, tmp_path, capsys):
    """Review rbal202b M1 probe 1: another session (or a person) removes the mark after the
    close, so the tool's unmark is answered 404 (exit 2); the same command run again writes
    nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    close = tracker.close

    def close_then_unmark_elsewhere(number, reason):
        close(number, reason)
        held = tracker.cards_by_number[number]
        tracker.cards_by_number[number] = replace(
            held, labels=tuple(label for label in held.labels if label != FILING))

    tracker.close = close_then_unmark_elsewhere
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    assert "Label does not exist" in capsys.readouterr().err
    writes = list(tracker.writes)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, 1, False, False)]


def test_a_withdrawn_ruling_is_never_filed_over_until_a_person_restores_it(code, tmp_path,
                                                                             capsys):
    """R-BAL206: a ruling closed as not planned was withdrawn, a person's decision; its
    command run again refuses, writing nothing, and says how to restore it -- reopened and
    closed as completed, it is the filed ruling again."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    err = capsys.readouterr().err
    assert ("was withdrawn (closed as not planned, R-BAL206), so nothing is filed over it.  To "
            "record it after all, reopen it and close it as completed on the web\n") in err
    assert tracker.writes == writes
    _closed_by_hand(tracker, 2, "COMPLETED")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes == writes and _rulings(tracker) == [(2, 1, False, False)]


def test_a_ruling_withdrawn_before_its_link_landed_is_found_by_its_mark(code, tmp_path, capsys):
    """A ruling whose link failed is under no owner; withdrawn on the web, it is still
    marked, so the marked listing holds it and the re-run refuses; restored, the same
    command finishes it -- linked, closed already, unmarked -- with no second ruling."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 2
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert ("(closed as not planned, before its filing finished, R-BAL206)" in
            capsys.readouterr().err)
    assert tracker.writes == writes
    _closed_by_hand(tracker, 2, "COMPLETED")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert tracker.writes[len(writes):] == [("add_child", 1, 2), ("unmark", 2)]
    assert _rulings(tracker) == [(2, 1, False, False)]


def test_a_withdrawn_extra_never_hides_the_ruling_that_stands(code, tmp_path, capsys):
    """Closing an extra as a duplicate is how a person withdraws it, so a finished ruling
    beside a withdrawn copy is the one filed; two that stand are refused, and the remedy
    for a closed one is a close as not planned, which `plan drop` cannot make."""
    body = ruling_body("Where?", "Here.")
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "ruling", False), Child(3, "ruling", False)))
    for number in (2, 3):
        tracker.add(number, "ruling", body=body, title="Home", parent=1, is_open=False,
                    state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert ("2 cards have this kind, title and text (plan#2, plan#3): withdraw the extras -- "
            "`plan drop` an open one; reopen a closed one and close it as not planned on the "
            "web") in capsys.readouterr().err
    _closed_by_hand(tracker, 2, "DUPLICATE")
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert "plan#3 [ruling, balance] Home is filed already" in capsys.readouterr().out
    assert not tracker.writes


def test_a_ruling_a_person_moved_under_another_owner_is_not_found(code, tmp_path):
    """The one place the ruling search does not reach (``_same_filing``): a closed ruling a
    person re-homed is under no owner this command names, so the command files one under
    the owner it names."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(5, children=(Child(2, "ruling", False),))
    tracker.add(2, "ruling", body=ruling_body("Where?", "Here."), title="Home", parent=5,
                is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    assert _rulings(tracker) == [(2, 5, False, False), (6, 1, False, False)]


def test_a_card_dropped_while_its_command_ran_is_never_filed_over(code, tmp_path, capsys,
                                                                    monkeypatch):
    """The listing of marked cards holds the step open; the read by its number, a moment
    later, finds it closed: dropped while this command ran.  It was called filed already,
    "it carries no filing mark", which was false; it is refused, unwritten, saying how to
    restore it."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    dropped = replace(tracker.cards_by_number[1], is_open=False, state_reason="NOT_PLANNED",
                      touched_by_hand=True)
    listed = tracker.cards
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        number: dropped if number == 1 else card for number, card in listed(numbers).items()})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert ("was closed as not planned before its filing finished, so nothing is filed over "
            "it.  To file it after all, reopen it on the web, then run this again to finish "
            "its filing") in capsys.readouterr().err
    assert tracker.writes == writes


# -- (b) a leaf closed while still marked -------------------------------------------------

def _split_by_an_unfinished_leaf(tracker, code, tmp_path):
    """plan#1, on the board, split by leaf plan#2 whose unmark failed: plan#2 holds plan#1's
    place, still marked, and plan#1 is off the board."""
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    assert tracker.board.items == [2] and FILING in tracker.cards_by_number[2].labels


def test_a_person_closing_the_only_unfinished_leaf_leaves_a_plain_step(code, tmp_path,
                                                                          capsys):
    """Review rbal202b LOW 11: a person closes plan#1's only leaf on the web while it is
    still marked.  It was never part of the split, so plan#1 is a plain step again: never
    closed as not planned by `sync` (R-BAL187 counted it a split step whose leaves were all
    dropped), named by `next` as off the board, and put back on it by `plan move`."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes
    assert run(tracker, code, "next") == 0
    assert "NOT ON THE BOARD, so in no order: plan#1 " in capsys.readouterr().out
    assert run(tracker, code, "move", "plan#1", "--top") == 0
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.endswith("next: plan#1 [step, balance] card 1\n")
    assert run(tracker, code, "claim", "plan#1", "--branch", "feat/x") == 0


def test_a_read_that_lags_a_drops_unlink_never_drops_the_split_step(code, tmp_path):
    """Round 4 LOW 6: `plan drop` of the only unfinished leaf unlinks it, then closes it; a
    `sync` whose read of plan#1 still lists the link saw a split step whose only leaf was
    dropped, and closed it as not planned for good."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    assert run(tracker, code, "drop", "plan#2", "--why", "not splitting after all") == 0
    lagging = tracker.cards_by_number[1]
    tracker.cards_by_number[1] = replace(lagging, children=(Child(2, "step", False),))
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], parent=1)
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and tracker.cards_by_number[1].is_open


def test_a_leaf_that_shipped_counts_however_it_was_closed_while_marked(code, tmp_path,
                                                                          capsys):
    """Review rbal202b2 M-B(1): the unfinished leaf ships (a ``Ships:`` needs no claim) and
    a person closes it, as `sync` asks; its work is done, so plan#1 is a split step whose
    leaves are done -- closed as completed by `sync`, never offered again."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    ship(code, "Ships: plan#2")
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "completed")
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out == "next: nothing\n"


def test_an_open_unfinished_leaf_still_splits_its_step(code, tmp_path, capsys):
    """Review rbal202b3 M-C: a leaf whose filing is under way (open, marked) counts, so its
    split step is not offered as work, and is refused a board place, mid-filing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert "NOT ON THE BOARD" not in capsys.readouterr().out
    assert run(tracker, code, "move", "plan#1", "--top") == 1
    assert "no step split into leaves" in capsys.readouterr().err


def test_dropping_a_split_step_beside_a_finished_leaf_leaves_its_close_to_sync(code, tmp_path):
    """R-BAL190: a finished leaf carries the split step's drop, so the drop closes only the
    leaves; `sync` then closes plan#1 as not planned (R-BAL187), its unfinished leaf no
    leaf of it once closed."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "First")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2), ("comment", 3), ("close", 3)]
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")


def test_a_split_step_left_bare_by_its_drop_is_closed_after_its_leaves(code, tmp_path):
    """No finished leaf carries the drop of a step split only by an unfinished leaf, so the
    drop closes the step itself, last; if that close fails, the same drop run again finds
    a plain step and drops it."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    close = tracker.close

    def close_failing_for_the_split_step(number, reason):
        if number == 1 and not getattr(close_failing_for_the_split_step, "failed", False):
            close_failing_for_the_split_step.failed = True
            raise GitHubError(502, "bad gateway")
        close(number, reason)

    tracker.close = close_failing_for_the_split_step
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 2
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2)]
    assert tracker.cards_by_number[1].is_open
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert tracker.writes[-2:] == [("comment", 1, "Dropped: replanned"),
                                   ("close", 1, "not_planned")]


def test_a_step_left_bare_below_a_split_step_is_dropped_and_the_split_step_left_to_sync(
        code, tmp_path):
    """A step left bare under a split step is dropped itself, with the reason, and the
    split step above it, still split by it, is left to `sync` (R-BAL187)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Middle")) == 0
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Bottom", "plan#2")) == 2
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 3), ("close", 3), ("comment", 2), ("close", 2)]
    assert tracker.cards_by_number[1].is_open
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")


def test_an_unfinished_leaf_beside_one_closed_while_marked_is_its_steps_only_leaf(code,
                                                                                    tmp_path):
    """R-BAL205: dropping plan#3, whose unmark failed, puts plan#1 back on the board in its
    place, since plan#2 -- closed by a person while still marked -- was never a leaf."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 2)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Other")) == 2
    assert 1 not in tracker.board.items
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#3", "--why", "not splitting after all") == 0
    assert tracker.writes[len(writes):len(writes) + 3] == [
        ("board_add", 1), ("board_place", 1, "PVTI_3"), ("remove_child", 1, 3)]


# -- the delta round: leaf C's review (plan-tracker-2026-10-05/leafc_review.md) -----------

def test_a_card_dropped_while_marked_is_refused_whatever_the_listings_show(code, tmp_path,
                                                                             capsys):
    """Review M3 (P1, P1b): a card closed while still marked is found by its mark, so its
    command run again is refused with the same advice whether or not a listing lags; a
    current listing filed a second card, and a second leaf beside the closed one."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    tracker.add(2)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 2
    _closed_by_hand(tracker, 3)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "plan#1 [step, balance] Alone has this kind, title and text, and was closed as not " \
           "planned before its filing finished" in capsys.readouterr().err
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 1
    assert "To file it after all, reopen it on the web" in capsys.readouterr().err
    assert tracker.writes == writes
    tracker.cards_by_number[3] = replace(tracker.cards_by_number[3], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#2")) == 0
    assert tracker.writes[len(writes):] == [("unmark", 3)]


def test_a_withdrawn_ruling_is_refused_as_withdrawn_whatever_its_labels(code, tmp_path,
                                                                         capsys):
    """Review LOW 1 (P2): the label check ran first, so a withdrawn ruling relabelled since
    was told "relabel it by hand"; the withdrawal is the reason nothing is filed."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2],
                                         labels=("balance", "moves-money"))
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    err = capsys.readouterr().err
    assert "was withdrawn (closed as not planned, R-BAL206)" in err and "relabel" not in err


def test_a_finding_closed_after_its_filing_finished_is_filed_anew(code):
    """The boundary ``_same_filing`` states (review LOW 4, P6): only a card still marked,
    or a ruling under its owner, is looked for among closed cards; a finding dropped after
    its filing finished is not, and the same command files another."""
    args = ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
            "--text", "The report counts a refund twice.")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *args) == 0
    assert run(tracker, code, "drop", "plan#2", "--why", "not a defect") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, *args) == 0
    assert tracker.writes[len(writes)][:2] == ("create", 3)


def test_a_sync_whose_open_listing_lags_a_leaf_closing_never_reopens_the_dropped_step(
        code, tmp_path, monkeypatch):
    """Review M1 (P7): `plan drop` closed the split step itself, its only leaf still being
    filed; a `sync` whose open listing still showed that leaf open took the split step for
    split again and reopened it, losing the drop once the listing caught up.  Each card
    sync decides over is read by its number."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    listed = tracker.open_cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and not tracker.cards_by_number[1].is_open


def test_a_drop_never_writes_over_a_persons_close_of_the_split_step(code, tmp_path):
    """Review LOW 2 (P5): a split step a person already closed keeps their close; the drop
    reaches its leaves only."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert [write[:2] for write in tracker.writes[len(writes):]] == [
        ("comment", 1), ("comment", 2), ("close", 2)]
    assert tracker.cards_by_number[1].touched_by_hand


def test_a_new_leaf_waits_on_an_unfinished_leaf_split_below_by_hand(code, tmp_path):
    """Review M2 (P3b): an OPEN marked leaf counts even when a step a person linked under it
    was dropped, so R-BAL204 refuses a new leaf beside it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1, labels=("balance", FILING), children=(Child(3, "step", False),))
    tracker.add(3, parent=2, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True,
                on_board=False)
    assert run(tracker, code, *leaf_filing(tmp_path, "Beside")) == 1
    assert tracker.cards_by_number[1].step_children == (2,)


# -- the third round: leaf C's delta review (plan-tracker-2026-10-05/leafc_review_delta.md) --

def test_shipped_work_closed_while_marked_is_never_refused_nor_filed_again(code, tmp_path,
                                                                           capsys):
    """Delta DM1 (D2): a step's filing stops before its unmark, it ships, and a person closes
    it as `sync` asks.  It was not dropped (git), so its command run again was refused as
    if a decision ended it and told to REOPEN shipped work; it now writes nothing and says
    it shipped."""
    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    args = ("file", "step", "--arc", "balance", "--title", "Alone", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *args) == 2
    ship(code, "Ships: plan#1")
    _closed_by_hand(tracker, 1)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    out = capsys.readouterr().out
    assert "shipped in git" in out and "reopen" not in out
    assert tracker.writes == writes


def test_a_drop_closes_a_split_step_the_tool_had_shown_completed(code, tmp_path):
    """Delta DM3 (D6): only a PERSON's close is kept.  The tool showed plan#1 completed
    (its only leaf shipped); a ``Reopens:`` undid the leaf and a person reopened it; a
    drop of plan#1 skipped it as closed, and the next `sync` reopened it as a plain step,
    losing the drop."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    ship(code, "Ships: plan#2")
    _closed_by_hand(tracker, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "completed")
    ship(code, "Reopens: plan#2")
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes and not tracker.cards_by_number[1].is_open


def test_a_listed_card_its_number_cannot_read_fails_sync_unwritten(code, tmp_path,
                                                                    monkeypatch, capsys):
    """Delta LOW 1 (D4): a card the listing holds but a read by its number does not kept
    its stale listing copy, which decided sync; sync now fails closed, writing nothing."""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    listed, by_number = tracker.open_cards, tracker.cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    monkeypatch.setattr(tracker, "cards", lambda numbers: {
        n: c for n, c in by_number(numbers).items() if n != 2})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 2
    assert "plan#2 is listed, but a read by its number does not hold it" in (
        capsys.readouterr().err)
    assert tracker.writes == writes


def test_a_leaf_dropped_with_its_split_step_is_refused_for_its_drop_under_any_parent(
        code, tmp_path, capsys):
    """Delta LOW 2 (D3): the re-homing refusal came first, so a re-plan filing the same leaf
    under a new step was told "re-homing a card is done by hand"; the drop is the reason.
    (The mark does not record the parent, so identical leaf text is refused under any.)"""
    tracker = FakeTracker()
    _split_by_an_unfinished_leaf(tracker, code, tmp_path)
    assert run(tracker, code, "drop", "plan#1", "--why", "replanned") == 0
    tracker.add(9)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half", "plan#9")) == 1
    err = capsys.readouterr().err
    assert "before its filing finished" in err and "re-homing" not in err


def test_a_lagging_listing_turns_a_new_filing_into_filed_already(code, monkeypatch, capsys):
    """Delta LOW 3 (D5), the boundary ``_same_filing`` states: a finding closed after its
    filing finished is not looked for, but a listing that lags its close still shows it
    open; read closed by its number, it is filed already and nothing is written."""
    args = ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
            "--text", "The report counts a refund twice.")
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *args) == 0
    stale = tracker.cards([2])[2]
    assert run(tracker, code, "drop", "plan#2", "--why", "not a defect") == 0
    listed = tracker.open_cards
    monkeypatch.setattr(tracker, "open_cards", lambda: {**listed(), 2: stale})
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 0
    assert "is filed already" in capsys.readouterr().out and tracker.writes == writes


# -- the fourth round: leaf C's round 3 (plan-tracker-2026-10-05/leafc_review_r3.md) ------

def test_a_stray_ships_trailer_never_decides_a_withdrawn_ruling(code, tmp_path, capsys):
    """Round 3 RM1 (R1): a ruling is never shipped (R-BAL184: a ``Ships:`` naming one is not
    acted on), so a stray trailer must not turn its withdrawal into "filed already"."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 0
    _closed_by_hand(tracker, 2, "NOT_PLANNED")
    ship(code, "Ships: plan#2")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *ruling_filing(tmp_path, "plan#1")) == 1
    assert "was withdrawn (closed as not planned, R-BAL206)" in capsys.readouterr().err
    assert tracker.writes == writes


def test_a_stray_ships_trailer_never_decides_a_question_dropped_while_marked(code, tmp_path,
                                                                              capsys):
    """Round 3 RM1 (R2): a question is never shipped either, so one closed while still
    marked is refused as dropped, never told it "shipped in git"."""
    spec = tmp_path / "q.md"
    spec.write_text("Which day?")
    args = ("file", "question", "--arc", "balance", "--title", "Day", "--body-file", str(spec))
    tracker = FakeTracker()
    FailOnce(tracker.board, "add")
    assert run(tracker, code, *args) == 2
    _closed_by_hand(tracker, 1, "NOT_PLANNED")
    ship(code, "Ships: plan#1")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *args) == 1
    assert "was closed as not planned before its filing finished" in capsys.readouterr().err
    assert tracker.writes == writes
