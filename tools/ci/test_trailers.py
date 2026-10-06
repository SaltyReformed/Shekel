"""What git says shipped, read from throwaway repositories under pytest's tmp_path; no network.

Moved here with :mod:`tools.ci.trailers` from ``tools/plan/test__git.py`` by
step X-cx's L4; the tracker tool's own git calls are tested there still.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import GitError, git
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import run as _run
from tools.ci.trailers import DEV, history, is_ancestor, shipped


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=dev")
    return root


def _dev(root, sha):
    """Point the remote-tracking ``dev`` the plan reads at ``sha``."""
    _run(root, "update-ref", "refs/remotes/origin/dev", sha)


def test_only_the_trailer_block_claims_a_card(repo):
    """A sentence naming plan#3 claims nothing; the last paragraph's trailers do."""
    base = _commit(repo, "base")
    prose = _commit(repo, "fix", "Ships: plan#3 was the wrong card to cite here.",
                    "Co-Authored-By: A <a@example.invalid>", parents=[base])
    leaf = _commit(repo, "leaf", "Why it changed.",
                   "Ships: plan#4\nShips: plan#5\nCo-Authored-By: A <a@example.invalid>",
                   parents=[prose])
    _dev(repo, leaf)
    found = history(repo)
    assert [(t.key, t.card) for t in found.trailers] == [("Ships", 4), ("Ships", 5)]
    assert {t.sha for t in found.trailers} == {leaf}
    assert set(shipped(repo, found)[0]) == {4, 5}


def test_the_old_form_is_left_to_the_plan_gate_and_anything_else_is_reported(repo):
    """``balance:X-1`` is the plan gate's until the cutover; every other value names nothing."""
    leaf = _commit(repo, "leaf", "Ships: balance:X-bi-6-4d-1\nShips: pay_calendar:C21\n"
                                 "Ships: plan #7\nShips: Plan#8\nShips: #9\nShips: pla#10\n"
                                 "Ships: banking:X-1\nReopens: plan#9a")
    _dev(repo, leaf)
    found = history(repo)
    assert not found.trailers
    assert [line.split(" ", 1)[1] for line in found.malformed] == [
        "Ships: 'plan #7' is not plan#<number>",
        "Ships: 'Plan#8' is not plan#<number>",
        "Ships: '#9' is not plan#<number>",
        "Ships: 'pla#10' is not plan#<number>",
        "Ships: 'banking:X-1' is not plan#<number>",
        "Reopens: 'plan#9a' is not plan#<number>",
    ]


def test_a_reopen_cancels_the_ships_it_was_built_on_and_a_reship_stands(repo):
    """Ships, Reopens, Ships again: shipped, by the last commit only."""
    first = _commit(repo, "ship", "Ships: plan#2")
    undo = _commit(repo, "revert", "Reopens: plan#2", parents=[first])
    _dev(repo, undo)
    assert shipped(repo, history(repo)) == ({}, ())
    again = _commit(repo, "ship again", "Ships: plan#2", parents=[undo])
    _dev(repo, again)
    standing, strays = shipped(repo, history(repo))
    assert [t.sha for t in standing[2]] == [again] and not strays


def test_a_ship_the_reopen_never_saw_still_stands(repo):
    """Ancestry, not date: a parallel branch's Ships merged after the Reopens stands."""
    base = _commit(repo, "base")
    first = _commit(repo, "ship", "Ships: plan#6", parents=[base])
    undo = _commit(repo, "revert", "Reopens: plan#6", parents=[first])
    parallel = _commit(repo, "ship elsewhere", "Ships: plan#6", parents=[base])
    merge = _commit(repo, "merge", parents=[undo, parallel])
    _dev(repo, merge)
    assert [t.sha for t in shipped(repo, history(repo))[0][6]] == [parallel]


def test_a_reopen_that_cancels_nothing_is_a_stray_and_the_card_stays_shipped(repo):
    """R-BAL181: a branch started before #7 shipped never held its code; its Reopens is
    reported, never acted on -- most likely a mistyped number."""
    base = _commit(repo, "base")
    ship = _commit(repo, "ship", "Ships: plan#7", parents=[base])
    early = _commit(repo, "fix", "Reopens: plan#7\nReopens: plan#71", parents=[base])
    _dev(repo, _commit(repo, "merge", parents=[ship, early]))
    standing, strays = shipped(repo, history(repo))
    assert [t.sha for t in standing[7]] == [ship]
    assert sorted((t.card, t.sha) for t in strays) == [(7, early), (71, early)]


def test_a_commit_that_ships_and_reopens_the_same_card_leaves_it_open(repo):
    """A commit is in its own history, so its Reopens cancels its own Ships."""
    both = _commit(repo, "confused", "Ships: plan#4\nReopens: plan#4")
    _dev(repo, both)
    assert shipped(repo, history(repo)) == ({}, ())


def test_only_dev_is_read(repo):
    """A Ships trailer on a branch not yet merged into dev has shipped nothing."""
    base = _commit(repo, "base")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "leaf", "Ships: plan#3",
                                                           parents=[base]))
    _dev(repo, base)
    assert not history(repo).trailers
    assert [t.card for t in history(repo, "feature").trailers] == [3]


def test_a_range_reads_only_the_commits_it_names(repo):
    """``base..head`` is a change set: the commits ``head`` holds and ``base`` does not.

    CI's ``commit_trailers`` check reads a pull request's own commits this way,
    so a trailer already on ``base`` is not read again.
    """
    base = _commit(repo, "base", "Ships: plan#2")
    leaf = _commit(repo, "leaf", "Ships: plan#3", parents=[base])
    assert [(t.card, t.sha) for t in history(repo, f"{base}..{leaf}").trailers] == [(3, leaf)]
    assert {t.card for t in history(repo, leaf).trailers} == {2, 3}


def test_a_shallow_clone_is_refused_not_read_as_nothing_shipped(repo, tmp_path):
    """A depth-1 clone holds no commit before its tip: every older ship would vanish."""
    base = _commit(repo, "base", "Ships: plan#19")
    _run(repo, "update-ref", "refs/heads/dev", _commit(repo, "tip", parents=[base]))
    shallow = tmp_path / "shallow"
    _run(tmp_path, "clone", "--quiet", "--depth=1", "--branch=dev", f"file://{repo}",
         str(shallow))
    with pytest.raises(GitError, match="shallow clone"):
        history(shallow)


def test_a_missing_branch_is_an_error_not_an_empty_history(repo):
    """An unfetched dev must not read as "nothing shipped"."""
    with pytest.raises(GitError, match="origin/dev"):
        history(repo, DEV)


def test_is_ancestor_raises_on_a_commit_git_does_not_have(repo):
    """Exit 128 is an error, never "not an ancestor"."""
    base = _commit(repo, "base")
    with pytest.raises(GitError):
        is_ancestor(repo, "0" * 40, base)


# -- an environment bound to another repository reaches nothing but the directory named ----------

@pytest.fixture(name="bound", params=scratch.BINDINGS)
def _bound(request, tmp_path, monkeypatch):
    """A sentinel repository this process's environment is bound to; it and its state."""
    return scratch.bound_sentinel(tmp_path, request.param, monkeypatch.setenv)


def test_a_bound_environment_reaches_no_repository_but_the_one_named(bound, tmp_path):
    """The 2026-10-04 incident's mechanism replayed onto a sentinel: every write the tests'
    builder makes and every git call this package makes lands in the directory named,
    every answer is that directory's, and the sentinel -- its refs, config, HEADs and
    indexes -- is byte-identical afterwards.  The sentinel's indexes each hold a committed
    file, so an index a leaked ``GIT_INDEX_FILE`` rewrote with this test's empty tree
    differs from it (review cp3 M-1: with an empty index, the rewrite was byte-identical
    and passed).  The tracker tool's own calls are held the same way in
    ``tools/plan/test__git.py``."""
    sentinel, before = bound
    assert b"work.txt" in before["index"] and b"work.txt" in before["lane/index"], (
        "control: each index the sentinel holds is not empty")
    plain = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--absolute-git-dir"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert Path(plain).resolve().is_relative_to((sentinel / ".git").resolve()), (
        "control: the planted environment binds a plain git call to the sentinel")

    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "--quiet", "--initial-branch=dev")
    leaf = _commit(work, "leaf", "Ships: plan#3")
    _run(work, "update-ref", "refs/heads/dev", leaf)
    assert git(work, "rev-parse", "dev").strip() == leaf
    assert [(t.card, t.sha) for t in history(work, "dev").trailers] == [(3, leaf)]
    assert is_ancestor(work, leaf, leaf)

    assert scratch.sentinel_state(sentinel) == before
