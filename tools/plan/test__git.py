"""The tracker tool's own git calls, read from throwaway repositories under pytest's tmp_path.

What a card's trailers mean (``Ships:``, ``Reopens:``, shipped) moved with
:mod:`tools.ci.trailers` to ``tools/ci/test_trailers.py`` (step X-cx's L4).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import GitError
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import run as _run
from tools.ci.trailers import history
from tools.plan import _git


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


# -- an environment bound to another repository reaches nothing but the directory named ----------

@pytest.fixture(name="bound", params=scratch.BINDINGS)
def _bound(request, tmp_path, monkeypatch):
    """A sentinel repository this process's environment is bound to; it and its state."""
    return scratch.bound_sentinel(tmp_path, request.param, monkeypatch.setenv)


def test_a_bound_environment_reaches_no_repository_but_the_one_named(bound, tmp_path):
    """The incident's mechanism replayed onto a sentinel: every write the tests' builder makes
    and every git call this module makes lands in the directory named, every answer is that
    directory's, and the sentinel -- its refs, config, HEADs and indexes -- is byte-identical
    afterwards.  The sentinel's indexes each hold a committed file, so an index a leaked
    ``GIT_INDEX_FILE`` rewrote with this test's empty tree differs from it (review cp3
    M-1: with an empty index, the rewrite was byte-identical and passed).  The trailer
    rules' own calls, ``is_ancestor`` among them, are held the same way in
    ``tools/ci/test_trailers.py``."""
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
    assert _git.author_date(work, leaf)
    _run(tmp_path, "clone", "--quiet", "--branch=dev", f"file://{origin}", str(tmp_path / "clone"))
    assert _git.current_branch(tmp_path / "clone") == "dev"
    _run(work, "checkout", "--quiet", "--detach", leaf)
    assert _git.current_branch(work) is None

    assert scratch.sentinel_state(sentinel) == before


def test_a_rebased_branch_starts_at_its_earliest_own_commit(repo, monkeypatch):
    """Review cp4b L5: a rebase keeps an own commit's author date and moves it onto a newer
    base, so the base's date alone opened the window after the commit was written."""
    dated = {}
    for name, when, parents in (("d0", "2026-09-01T00:00:00-04:00", ()),
                                ("d9", "2026-09-09T00:00:00-04:00", ("d0",)),
                                ("b1", "2026-09-02T00:00:00-04:00", ("d9",))):
        monkeypatch.setenv("GIT_AUTHOR_DATE", when)
        dated[name] = _commit(repo, name, parents=[dated[p] for p in parents])
    _dev(repo, dated["d9"])
    _run(repo, "update-ref", "refs/heads/feature", dated["b1"])
    assert _git.started(repo, "feature") == "2026-09-02T00:00:00-04:00"


def test_the_start_is_the_earliest_moment_whatever_each_dates_offset(repo, monkeypatch):
    """Review cp4b G4: dates written in different offsets compare as moments, not as text
    (``+05:00`` 09-02 01:00 is 09-01 20:00 UTC, before ``-04:00`` 09-01 22:00)."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-01T22:00:00-04:00")
    base = _commit(repo, "dev")
    _dev(repo, base)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-02T01:00:00+05:00")
    _run(repo, "update-ref", "refs/heads/feature", _commit(repo, "own", parents=[base]))
    assert _git.started(repo, "feature") == "2026-09-02T01:00:00+05:00"
