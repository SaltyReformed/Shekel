"""What git says shipped, read from throwaway repositories under pytest's tmp_path; no network."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import _git
from _git import DEV, GitError, history, shipped
from _scratch import run as _run


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=dev")
    return root


def _commit(root, *paragraphs, parents=()):
    """A commit of the empty tree whose message is ``paragraphs``; its sha."""
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    args = [arg for parent in parents for arg in ("-p", parent)]
    args += [arg for paragraph in paragraphs for arg in ("-m", paragraph)]
    return _run(root, "commit-tree", tree, *args)


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
        _git.is_ancestor(repo, "0" * 40, base)


def test_current_branch_and_a_detached_head(repo):
    """A claim names a branch, so a detached HEAD has none to name."""
    base = _commit(repo, "base")
    _run(repo, "update-ref", "refs/heads/dev", base)
    assert _git.current_branch(repo) == "dev"
    _run(repo, "checkout", "--quiet", "--detach", base)
    assert _git.current_branch(repo) is None
    with pytest.raises(GitError):
        _git.current_branch(repo / "no-such-directory")


def test_pushed_asks_origin(repo, tmp_path):
    """A branch is pushed when origin holds it, whatever this clone has fetched."""
    origin = tmp_path / "origin.git"
    _run(tmp_path, "init", "--quiet", "--bare", str(origin))
    _run(repo, "remote", "add", "origin", str(origin))
    _run(repo, "update-ref", "refs/heads/feat/x", _commit(repo, "base"))
    assert not _git.pushed(repo, "feat/x")
    _run(repo, "push", "--quiet", "origin", "feat/x")
    assert _git.pushed(repo, "feat/x")


@pytest.mark.parametrize("url", [
    "https://github.com/SaltyReformed/Shekel.git",
    "https://github.com/SaltyReformed/Shekel",
    "git@github.com:SaltyReformed/Shekel.git",
])
def test_origin_repository_reads_either_url_form(repo, url):
    """The pull-request lookup needs ``owner/name``; L5 changes the owner."""
    _run(repo, "remote", "add", "origin", url)
    assert _git.origin_repository(repo) == "SaltyReformed/Shekel"


def test_a_branch_starts_at_the_commit_it_grew_from(repo, monkeypatch):
    """Review cp3 M-2: ``spec-history --since <branch>`` starts where the work started, the
    author date of the dev commit the branch's oldest own commit sits on; a branch with no
    commit off dev is its own start."""
    dated = {}
    for name, when, parents in (("fork", "2026-09-30T12:00:00-04:00", ()),
                                ("first", "2026-10-01T09:00:00-04:00", ("fork",)),
                                ("dev", "2026-10-02T09:00:00-04:00", ("fork",)),
                                ("tip", "2026-10-03T09:00:00-04:00", ("first",))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["dev"])
    _run(repo, "update-ref", "refs/heads/feature", dated["tip"])
    assert _git.started(repo, "feature") == "2026-09-30T12:00:00-04:00"
    _run(repo, "update-ref", "refs/heads/fresh", dated["dev"])
    assert _git.started(repo, "fresh") == "2026-10-02T09:00:00-04:00"


def test_a_branch_holding_a_merge_starts_at_the_earliest_line_it_grew_from(repo, monkeypatch):
    """Review cp4 L-5: a merge listing a peer line FIRST put that line's base first, so the
    branch's own commit b1 fell outside the window; every line it grew from counts."""
    dated = {}
    for name, when, parents in (("d0", "2026-09-01T00:00:00-04:00", ()),
                                ("b1", "2026-09-02T00:00:00-04:00", ("d0",)),
                                ("b2", "2026-09-03T00:00:00-04:00", ("b1",)),
                                ("d5", "2026-09-05T00:00:00-04:00", ("d0",)),
                                ("p1", "2026-09-06T00:00:00-04:00", ("d5",)),
                                ("p2", "2026-09-07T00:00:00-04:00", ("p1",)),
                                ("m", "2026-09-09T00:00:00-04:00", ("p2", "b2"))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d5"])
    _run(repo, "update-ref", "refs/heads/feature", dated["m"])
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"
    _run(repo, "update-ref", "refs/heads/feature",
         _commit(repo, "m2", parents=[dated["b2"], dated["p2"]]))
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_a_branch_with_a_root_of_its_own_starts_at_that_root(repo, monkeypatch):
    """A line no dev commit lies under (an orphan root) has no boundary; its root is where
    that line's work started."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-10T00:00:00-04:00")
    _dev(repo, _commit(repo, "dev"))
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-01T00:00:00-04:00")
    root = _commit(repo, "orphan")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-05T00:00:00-04:00")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "work", parents=[root]))
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_a_branch_starts_at_the_earliest_of_every_line_it_grew_from(repo, monkeypatch):
    """A dev commit under one line and an orphan root under another: the earlier of the two,
    whichever git lists last."""
    dated = {}
    for name, when, parents in (("d", "2026-09-01T00:00:00-04:00", ()),
                                ("b1", "2026-09-03T00:00:00-04:00", ("d",)),
                                ("r", "2026-09-05T00:00:00-04:00", ()),
                                ("w", "2026-09-06T00:00:00-04:00", ("r",)),
                                ("m", "2026-09-07T00:00:00-04:00", ("w", "b1"))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d"])
    _run(repo, "update-ref", "refs/heads/feature", dated["m"])
    assert _git.started(repo, "feature") == "2026-09-01T00:00:00-04:00"


def test_author_date_is_when_the_commit_was_first_written(repo, monkeypatch):
    """``spec-history --since <ref>`` reads the AUTHOR date, ISO with its offset: a rebase
    or amend moves the committer date later and would narrow the window."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T09:00:00-04:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-03T09:00:00-04:00")
    sha = _commit(repo, "base")
    assert _git.author_date(repo, sha) == "2026-10-01T09:00:00-04:00"


def test_repository_root_is_the_checkout_holding_this_package():
    """``tools/plan/_git.py`` -> the root two levels above ``tools``."""
    assert (_git.repository_root() / "tools" / "plan" / "_git.py").is_file()


# -- an environment bound to another repository reaches nothing but the directory named ----------

def _sentinel_state(sentinel):
    """What the 2026-10-04 incident changed, or could have: every ref, the config, each HEAD
    and index of the main checkout and of its linked worktree ``lane``."""
    gitdir = sentinel / ".git"
    files = (gitdir / "config", gitdir / "HEAD", gitdir / "index",
             gitdir / "worktrees" / "lane" / "HEAD", gitdir / "worktrees" / "lane" / "index")
    return {"refs": _run(sentinel, "for-each-ref"),
            **{f.name if f.parent == gitdir else f"lane/{f.name}":
               f.read_bytes() if f.exists() else None for f in files}}


@pytest.fixture(name="bound", params=["hook in a linked worktree", "git-dir and work-tree"])
def _bound(request, tmp_path, monkeypatch):
    """A SENTINEL repository with a linked worktree, standing in for the developer's shared
    one, and this process's environment bound to it; the sentinel and its state.

    The first binding is what git hands a pre-commit hook run from a linked
    worktree (measured 2026-10-04, git 2.56, by a hook that dumped its
    environment: an absolute ``GIT_DIR`` at the worktree's gitdir, its
    ``GIT_INDEX_FILE``, an empty ``GIT_PREFIX``); the second is the main
    checkout bound by ``--git-dir`` and ``--work-tree``.
    """
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    _run(sentinel, "init", "--quiet", "--initial-branch=main")
    (sentinel / "work.txt").write_text("the developer's file\n", encoding="utf-8")
    _run(sentinel, "add", "work.txt")
    _run(sentinel, "commit", "--quiet", "-m", "the developer's commit")
    _run(sentinel, "worktree", "add", "--quiet", "-b", "lane", str(tmp_path / "lane"))
    gitdir = sentinel / ".git"
    if request.param == "hook in a linked worktree":
        planted = {"GIT_DIR": gitdir / "worktrees" / "lane",
                   "GIT_INDEX_FILE": gitdir / "worktrees" / "lane" / "index", "GIT_PREFIX": ""}
    else:
        planted = {"GIT_DIR": gitdir, "GIT_WORK_TREE": sentinel,
                   "GIT_INDEX_FILE": gitdir / "index"}
    state = _sentinel_state(sentinel)
    for name, value in planted.items():
        monkeypatch.setenv(name, str(value))
    return sentinel, state


def test_a_bound_environment_reaches_no_repository_but_the_one_named(bound, tmp_path):
    """The incident's mechanism replayed onto a sentinel: every write the tests' builder makes
    and every git call this module makes lands in the directory named, every answer is that
    directory's, and the sentinel -- its refs, config, HEADs and indexes -- is byte-identical
    afterwards.  The sentinel's indexes each hold a committed file, so an index a leaked
    ``GIT_INDEX_FILE`` rewrote with this test's empty tree differs from it (review cp3
    M-1: with an empty index, the rewrite was byte-identical and passed)."""
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
    assert _git.current_branch(work) == "dev"
    origin = tmp_path / "origin.git"
    _run(tmp_path, "init", "--quiet", "--bare", str(origin))
    _run(work, "remote", "add", "origin", str(origin))
    assert not _git.pushed(work, "dev")
    _run(work, "push", "--quiet", "origin", "dev")
    assert _git.pushed(work, "dev")
    _git.fetch(work)
    assert [(t.card, t.sha) for t in history(work).trailers] == [(3, leaf)]
    assert _git.is_ancestor(work, leaf, leaf)
    assert _git.author_date(work, leaf)
    _run(tmp_path, "clone", "--quiet", "--branch=dev", f"file://{origin}", str(tmp_path / "clone"))
    assert _git.current_branch(tmp_path / "clone") == "dev"
    _run(work, "checkout", "--quiet", "--detach", leaf)
    assert _git.current_branch(work) is None

    assert _sentinel_state(sentinel) == before
