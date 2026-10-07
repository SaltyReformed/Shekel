"""The ``quill`` command's reads and decisions -- next, claim, release, show, block, move,
drop, sync, spec-history and spec-revert -- over :class:`_fake.FakeTracker` and a
throwaway git repository (the ``code`` fixture, ``conftest.py``).  ``file`` has its own
module, ``test_quill_file.py``.  Nothing here calls GitHub.
"""
from __future__ import annotations

import pytest
import requests

from tools.ci.gitcmd import GitError
from tools.ci.scratch import point_dev
from tools.ci.scratch import run as _run
from tools.quill import _git
from tools.quill import quill
from tools.quill._fake import FakeTracker, run, ship
from tools.quill._github import GitHubError
from tools.quill._tracker import Child, Claim, Edit, OutsideLink


# -- next ----------------------------------------------------------------------------------

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



# -- claim, release ------------------------------------------------------------------------

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
    for child in fields.get("children", ()):
        tracker.add(child.number, parent=1)
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



# -- block, move, drop ---------------------------------------------------------------------

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



# -- spec-history, spec-revert -------------------------------------------------------------

def _versions(tracker):
    """Card 1 filed 10-01 by the tool, edited 10-02 by the tool and 10-03 by the developer."""
    tracker.add(1, body="v3")
    tracker.versions[1] = [
        Edit("E1", "2026-10-01T00:00:00Z", FakeTracker.app_login, "v1\n"),
        Edit("E2", "2026-10-02T00:00:00Z", FakeTracker.app_login, "v2\n"),
        Edit("E3", "2026-10-03T00:00:00Z", "SaltyReformed", "v3"),
    ]



def test_spec_history_shows_each_change_in_the_window_against_the_version_before(code, capsys):
    """R-BAL174: a review grades every spec change since its branch started."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-history", "plan#1", "--since", "2026-10-02") == 0
    out = capsys.readouterr().out
    assert f"edit E2, 2026-10-02T00:00:00Z by {FakeTracker.app_login}" in out
    assert "-v1" in out and "+v2" in out
    assert "edit E3" in out and "by SaltyReformed" in out
    assert "filed" not in out



def test_spec_history_since_a_git_ref_reads_its_commit_date(code, capsys):
    """``--since <branch>`` for a branch with no commit off dev yet: it starts at its tip,
    the commit it was cut from."""
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



# -- show, and the exit statuses -----------------------------------------------------------

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
        raise GitError("boom")

    monkeypatch.setattr(tracker, "cards", broken)
    assert run(tracker, code, "drop", "plan#1", "--why", "x") == 2
    assert "failed: boom" in capsys.readouterr().err



# -- the review of checkpoint 2 ------------------------------------------------------------

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



def test_dropping_a_split_step_takes_its_leaves_out_of_the_order(code, capsys):
    """R-BAL190 (narrowing R-BAL185 for ``quill drop``; review L2 first): ``quill drop``
    on a split step notes it there, then drops each open leaf, each with the reason, and the
    split step shows them at the next sync."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert tracker.writes == [
        ("comment", 1, "Dropped: superseded (its leaves still work: plan#2)"),
                              ("comment", 2, "Dropped: superseded"), ("close", 2, "not_planned")]
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")


def test_a_split_step_a_person_closes_by_hand_takes_its_leaves_out_of_the_order(code, capsys):
    """R-BAL185 as ruled for a person's close: recorded once, on the split step; its leaves
    inherit it, and sync lists those left open under it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False, is_open=False,
                state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(2, parent=1)
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "sync") == 1
    assert "REPORT: plan#2 is open under plan#1, which was dropped" in capsys.readouterr().out
    assert not tracker.writes


def test_a_split_step_with_no_open_leaf_is_not_dropped_again(code, capsys):
    """Its own state shows its leaves; with none open there is nothing to drop."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False),), on_board=False)
    tracker.add(2, parent=1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "again") == 1
    assert "no leaf below it that is still work" in capsys.readouterr().err
    assert not tracker.writes



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
                                "/home/x/.config/shekel-quill/app.json")

    assert quill.main(["next"], connect=no_credentials, root=code) == 2
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
    assert "`quill release plan#1 --unreadable`" in out and "--branch None" not in out
    assert run(tracker, code, "release", "plan#1", "--branch", "feat/work") == 1
    assert "pass --unreadable" in capsys.readouterr().err
    assert run(tracker, code, "release", "plan#2", "--unreadable") == 1
    assert run(tracker, code, "release", "plan#1", "--unreadable") == 0
    assert tracker.writes == [("release", 1)]


def test_a_claim_refused_by_one_whose_branch_cannot_be_read_says_so(code, capsys):
    """Leaf C3: the refusal named such a holder 'None since ' (no branch, no date); it names
    it as every other line does."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.held[1] = Claim(1, None, "", "s1")
    assert run(tracker, code, "claim", "plan#1") == 1
    assert capsys.readouterr().err == ("refused: plan#1 is already claimed by a branch that "
                                       "cannot be read since ?\n")



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


def test_a_stray_beside_a_reopens_that_cancels_is_still_printed(code, capsys):
    """BAL-608: one commit reopens plan#1, which shipped, and plan#2, which never did; the
    first cancels plan#1's ship, and sync and show still print the second as a stray."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(2)
    ship(code, "Ships: plan#1")
    ship(code, "Reopens: plan#1", "Reopens: plan#2")
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert "'Reopens: plan#2' cancels no Ships" in out and "'Reopens: plan#1'" not in out
    assert tracker.writes == [("reopen", 1)]
    assert run(tracker, code, "show", "plan#2") == 0
    assert "STRAY: " in capsys.readouterr().out



# -- the review of checkpoint 3 ------------------------------------------------------------

def test_a_detached_head_releases_no_claim_without_naming_it(code, monkeypatch, capsys):
    """Review cp3 L-a: with no branch checked out, a claim whose branch cannot be read matched
    (None == None) and was released without --unreadable."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.held[1] = Claim(1, None, "", "s1")
    monkeypatch.setattr(_git, "current_branch", lambda _root: None)
    assert run(tracker, code, "release", "plan#1") == 1
    err = capsys.readouterr().err
    assert "HEAD is detached" in err and "pass --unreadable" in err
    assert not tracker.writes
    assert run(tracker, code, "release", "plan#1", "--unreadable") == 0



def test_a_container_is_never_moved_back_into_the_order(code, capsys):
    """Review cp3 L-f: ``move`` put a split step back on the board, where its leaves stand."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, "move", "plan#1", "--top") == 1
    assert "no step split into leaves" in capsys.readouterr().err
    assert not tracker.writes



def test_a_command_line_of_no_form_exits_2_before_anything_is_read(code):
    """Review cp3 L-i: argparse's usage error exits 2, which the docstring now says."""
    with pytest.raises(SystemExit) as stopped:
        quill.main(["file", "finding", "--arc", "balance"], connect=FakeTracker, root=code)
    assert stopped.value.code == 2
    assert "argparse's usage error" in quill.__doc__



def test_spec_history_since_a_branch_starts_where_the_branch_grew_from(code, monkeypatch,
                                                                      capsys):
    """Review cp3 M-2: ``--since <branch>`` read its LAST commit, hiding every spec edit made
    after the work started; it starts at the commit the branch grew from."""
    tree = _run(code, "hash-object", "-t", "tree", "/dev/null")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-30T12:00:00+00:00")
    fork = _run(code, "commit-tree", tree, "-m", "dev")
    point_dev(code, fork)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T09:00:00+00:00")
    first = _run(code, "commit-tree", tree, "-p", fork, "-m", "work starts")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-03T09:00:00+00:00")
    _run(code, "update-ref", "refs/heads/feat/work",
         _run(code, "commit-tree", tree, "-p", first, "-m", "work continues"))
    tracker = FakeTracker()
    tracker.add(1, body="v3")
    tracker.versions[1] = [Edit("E1", "2026-09-29T00:00:00Z", FakeTracker.app_login, "v1"),
                           Edit("E2", "2026-09-30T18:00:00Z", "SaltyReformed", "v2"),
                           Edit("E3", "2026-10-02T00:00:00Z", "SaltyReformed", "v3")]
    assert run(tracker, code, "spec-history", "plan#1", "--since", "feat/work") == 0
    out = capsys.readouterr().out
    assert "edit E2" in out and "edit E3" in out and "edit E1" not in out



def test_a_split_step_whose_leaves_were_all_dropped_counts_as_dropped(code, tmp_path, capsys):
    """R-BAL187 (review cp3 L-e): what waits on it is released, sync closes it as NOT
    planned, never completed, and no new leaf is filed under it."""
    tracker = FakeTracker()
    tracker.add(7, children=(Child(8, "step", True),), on_board=False)
    tracker.add(8, parent=7)
    tracker.add(9, blocked_by=(7,))
    assert run(tracker, code, "drop", "plan#8", "--why", "re-planned") == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#9 ")
    assert run(tracker, code, "sync") == 0
    assert "every leaf dropped (R-BAL187)" in capsys.readouterr().out
    assert tracker.writes[-1] == ("close", 7, "not_planned")
    spec = tmp_path / "s"
    spec.write_text("Redo.")
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "L7c",
               "--parent", "plan#7", "--body-file", str(spec)) == 1
    assert "a step that is done" in capsys.readouterr().err
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes, "the drop is shown once"



def test_a_card_linked_outside_the_tracker_is_never_offered_and_is_reported(code, capsys):
    """R-BAL188: the card carrying the link waits, as does every leaf of a step blocked by an
    outside issue; next and sync report the link; every other card works as usual."""
    tracker = FakeTracker()
    outside = (OutsideLink("blocker", "saltyreformed-labs/Shekel#12"),)
    tracker.add(1, outside=outside, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, outside=(OutsideLink("sub-issue", "saltyreformed-labs/Shekel#13"),))
    tracker.add(4)
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.startswith("next: plan#4 ")
    assert ("OUTSIDE LINK: plan#1's blocker is saltyreformed-labs/Shekel#12, outside the "
            "tracker") in out and "OUTSIDE LINK: plan#3's sub-issue" in out
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert "REPORT: plan#1's blocker" in out and "REPORT: plan#3's sub-issue" in out



def test_dropping_a_step_names_every_open_leaf_below_it_and_no_container(code, capsys):
    """Review cp3: the note named only the dropped step's own children, and called a step
    split again a leaf; R-BAL190: every open leaf below is dropped, and no step split
    again, whose state shows its own leaves; review cp4e I: the line printed of the note."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1, children=(Child(4, "step", True),), on_board=False)
    tracker.add(3, parent=1)
    tracker.add(4, parent=2)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    out = capsys.readouterr().out
    assert "dropped plan#3 " in out and "dropped plan#4 " in out and "dropped plan#2 " not in out
    assert "commented on plan#1: dropping its leaves still work, plan#3, plan#4;" in out
    assert ("close", 2, "not_planned") not in tracker.writes
    assert ("comment", 1, "Dropped: superseded (its leaves still work: plan#3, plan#4)"
            ) in tracker.writes



def test_sync_reports_an_open_ruling(code, capsys):
    """Review cp3: a ruling whose close failed kept what waits on it blocked, unreported."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "ruling", True),))
    tracker.add(2, "ruling", parent=1)
    assert run(tracker, code, "sync") == 1
    assert "REPORT: ruling plan#2 is open" in capsys.readouterr().out
    assert not tracker.writes


def test_the_board_add_a_move_makes_is_printed_as_it_lands(code, capsys):
    """Every write is printed as it lands: a step not on the board is added, then placed."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, on_board=False)
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert capsys.readouterr().out.startswith("  board: added plan#2 ")
    assert tracker.writes == [("board_add", 2), ("board_place", 2, None)]


def test_show_names_a_cards_links_outside_the_tracker(code, capsys):
    """Review cp4 L-8: ``show`` said "parent: none" for a card whose parent is outside, and
    named none of the links that keep it from being offered."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("parent", "saltyreformed-labs/Shekel#5"),
                            OutsideLink("blocker", "saltyreformed-labs/Shekel#6")))
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert "  parent: saltyreformed-labs/Shekel#5 (outside the tracker)" in out
    assert "  blocker outside the tracker: saltyreformed-labs/Shekel#6" in out
    assert "parent: none" not in out


def test_a_card_linked_outside_the_tracker_cannot_be_claimed(code, capsys):
    """Review cp4 L-11: R-BAL188 never offers it as work, yet ``claim`` took it."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "outside the tracker" in capsys.readouterr().err
    assert not tracker.writes


def test_sync_dry_run_writes_no_close_as_not_planned(code, capsys):
    """Review cp4 M54: the dry run must not write R-BAL187's close either."""
    tracker = FakeTracker()
    tracker.add(7, children=(Child(8, "step", False),), on_board=False)
    tracker.add(8, parent=7, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert "would close as not planned" in capsys.readouterr().out
    assert not tracker.writes


def test_dropping_a_claimed_card_names_the_claim_it_leaves(code, capsys):
    """A drop writes no claim; the claim it leaves is named, with the command that releases it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.held[1] = Claim(1, "feat/one", "2026-10-04T12:00:00Z", "s1")
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert ("its claim by 'feat/one' stays: `quill release plan#1 --branch feat/one`"
            in capsys.readouterr().out)
    assert 1 in tracker.held


def test_dropping_a_split_step_never_drops_a_leaf_git_shipped(code, capsys):
    """Review cp4b L6: a leaf whose ``Ships:`` merged before sync closed it was dropped, and
    no sync rule shows a shipped leaf the tool closed as not planned; git's answer wins, and
    undoing shipped work is a ``Reopens:`` commit."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, parent=1)
    ship(code, "Ships: plan#2")
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert not [write for write in tracker.writes if write[1] == 2]
    assert ("close", 3, "not_planned") in tracker.writes
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "superseded") == 1
    assert "'Reopens: plan#2', not a drop" in capsys.readouterr().err


class _FailOnComment:
    """``tracker.comment`` failing once, on the card numbered ``number``."""

    def __init__(self, tracker, number):
        """Stand in for ``tracker.comment``."""
        self.real, self.number, self.failed = tracker.comment, number, False
        tracker.comment = self

    def __call__(self, number, text):
        """Fail the first comment on ``self.number``."""
        if number == self.number and not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return self.real(number, text)


def test_a_split_step_drop_cut_short_at_its_note_is_finished_by_the_same_command(code):
    """Review cp4b L7: with the note written last, a retry after every leaf landed found no
    open leaf and never wrote it; the note goes first."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    _FailOnComment(tracker, 1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 2
    assert not tracker.writes
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert tracker.writes == [
        ("comment", 1, "Dropped: superseded (its leaves still work: plan#2)"),
                              ("comment", 2, "Dropped: superseded"), ("close", 2, "not_planned")]


def test_a_leaf_held_back_from_above_cannot_be_claimed(code, capsys):
    """Review cp4b L10: ``claim`` refused only the card's own outside link; a leaf under a
    split step blocked from outside the tracker (R-BAL188), or closed by a person
    (R-BAL185), is never offered either."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False,
                outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    tracker.add(2, parent=1)
    tracker.add(3, children=(Child(4, "step", True),), on_board=False, is_open=False,
                state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(4, parent=3)
    assert run(tracker, code, "claim", "plan#2", "--branch", "feat/a") == 1
    assert "plan#1 above it waits on saltyreformed-labs/Shekel#6" in capsys.readouterr().err
    assert run(tracker, code, "claim", "plan#4", "--branch", "feat/b") == 1
    assert "plan#3 above it was dropped" in capsys.readouterr().err
    assert not tracker.writes


def test_show_names_an_outside_parent_once(code, capsys):
    """Review cp4b P11: the parent line names it; the outside-link lines name the rest."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("parent", "saltyreformed-labs/Shekel#5"),))
    assert run(tracker, code, "show", "plan#1") == 0
    assert capsys.readouterr().out.count("Shekel#5") == 1


def test_a_closed_card_is_not_dropped_again(code, capsys):
    """Review cp4b P14."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "again") == 1
    assert "already closed" in capsys.readouterr().err
    assert not tracker.writes


def test_dropping_a_step_that_owns_findings_drops_the_step(code):
    """Review cp4b P15: a step owning findings (and no step) is no split step (R-BAL177)."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "finding", True),))
    tracker.add(2, "finding", parent=1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert ("close", 1, "not_planned") in tracker.writes


def test_open_work_git_says_shipped_cannot_be_claimed(code, capsys):
    """Review cp4c L1: ``next`` never offers it; ``claim`` took it."""
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, "Ships: plan#1")
    assert run(tracker, code, "claim", "plan#1", "--branch", "feat/again") == 1
    assert "shipped in git" in capsys.readouterr().err
    assert not tracker.writes


def test_dropping_a_split_step_drops_a_leaf_git_revived_whatever_its_card_shows(code, capsys):
    """Review cp4c L2 and L6: the leaves were picked by their display, so a leaf the tool
    shows completed whose ``Reopens:`` merged was skipped, then reopened by sync and offered
    under the dropped split step; and a shipped leaf left was not named."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False), Child(3, "step", True),
                             Child(4, "step", False)), on_board=False)
    tracker.add(2, parent=1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(3, parent=1)
    tracker.add(4, parent=1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    ship(code, "Ships: plan#2", "Ships: plan#4")
    ship(code, "Reopens: plan#2")
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert ("close", 2, "not_planned") in tracker.writes
    assert "plan#4 shipped in git, so it is not dropped" in capsys.readouterr().out
    assert not [write for write in tracker.writes if write[1] == 4]


def test_a_card_that_is_not_work_named_by_a_ships_trailer_can_still_be_dropped(code):
    """Review cp4c P11: git's answer counts only for work; a question a mistyped ``Ships:``
    names is still the developer's to withdraw."""
    tracker = FakeTracker()
    tracker.add(1, "question")
    ship(code, "Ships: plan#1")
    assert run(tracker, code, "drop", "plan#1", "--why", "withdrawn") == 0
    assert ("close", 1, "not_planned") in tracker.writes
