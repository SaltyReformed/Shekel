"""The tracker tool's own git calls, read from throwaway repositories under pytest's tmp_path.

What a card's trailers mean (``Ships:``, ``Reopens:``) moved with :mod:`tools.ci.trailers` to
``tools/ci/test_trailers.py`` (step X-cx's L4); quill reads none since the ship marker (X-cx's
M1, ruling ``balance:R-BAL359``).  The marker and the claim are read and written here against a
real bare ``origin`` (:func:`_origin`), so the pushes, the lease and the fetch run as git runs
them.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tools.ci import scratch
from tools.ci.gitcmd import GitError
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import commit_files as _files
from tools.ci.scratch import point_dev as _dev
from tools.ci.scratch import run as _run
from tools.quill import _git

#: When the tests' claims are made, and when a later one is.
_MON, _TUE = datetime(2026, 10, 19, 13, 0, tzinfo=UTC), datetime(2026, 10, 20, 9, 30, tzinfo=UTC)


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """An empty repository under tmp_path (never a real checkout)."""
    return scratch.repository(tmp_path)


def _origin(tmp_path, work):
    """A bare ``origin`` for ``work``, holding ``dev`` and ``main`` at one base commit that
    ``work`` has fetched; the base's sha.  ``work``'s own configuration names its committer
    and signs nothing, so a claim's commit is made alike on every machine."""
    origin = tmp_path / "origin.git"
    _run(tmp_path, "init", "--quiet", "--bare", str(origin))
    _run(work, "remote", "add", "origin", str(origin))
    for key, value in (("user.name", "Plan Test"), ("user.email", "plan-test@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _run(work, "config", key, value)
    base = _files(work, _commit(work, "root"), "base", {"README": "base\n"})
    _run(work, "push", "--quiet", "origin", f"{base}:refs/heads/dev", f"{base}:refs/heads/main")
    _git.fetch(work)
    return base


def _push(work, sha, branch):
    """Point ``origin``'s ``branch`` at ``sha``, forced, as a merge or a push would."""
    _run(work, "push", "--quiet", "--force", "origin", f"{sha}:refs/heads/{branch}")


def _marker(number, when="2026-10-19T13:00:00Z"):
    """``{plan/ships/<number>: its line}``, a marker as a claim writes it."""
    return {_git.marker(number): f"claimed {when}\n"}


def test_what_shipped_is_the_merge_of_dev_and_main(repo, tmp_path):
    """R-BAL359: a hotfix's marker on ``main`` counts before ``main`` is merged into ``dev``; a
    marker ``dev`` deleted (a revert) is unshipped before the release; anything under
    ``plan/ships/`` that is no card's marker is a HISTORY line, never a card."""
    base = _origin(tmp_path, repo)
    both = _files(repo, base, "five shipped", _marker(5))
    _push(repo, both, "dev")
    _push(repo, both, "main")
    dev = _files(repo, both, "revert five, ship seven, and two strays",
                 {_git.marker(5): None, **_marker(7),
                  "plan/ships/notes": "x\n", "plan/ships/8/inner": "x\n"})
    _push(repo, dev, "dev")
    _push(repo, _files(repo, both, "hotfix nine", _marker(9)), "main")
    _git.fetch(repo)
    found = _git.shipped(repo)
    assert found.cards == {7, 9}
    assert [line.split(" ")[0] for line in found.history] == ["plan/ships/8/inner",
                                                             "plan/ships/notes"]


def test_a_conflict_on_a_marker_refuses_to_answer_and_one_elsewhere_does_not(repo, tmp_path):
    """Two claims' markers for one card meeting in the merge leave its record unknown, so
    ``shipped`` refuses; a conflict in code only leaves every marker readable."""
    base = _origin(tmp_path, repo)
    _push(repo, _files(repo, base, "dev", {"README": "dev\n", **_marker(4)}), "dev")
    _push(repo, _files(repo, base, "main", {"README": "main\n", **_marker(6)}), "main")
    _git.fetch(repo)
    assert _git.shipped(repo).cards == {4, 6}
    _push(repo, _files(repo, base, "main again", _marker(4, "2026-10-20T09:30:00Z")), "main")
    _git.fetch(repo)
    with pytest.raises(GitError, match="conflict on plan/ships/4"):
        _git.shipped(repo)


def test_a_claim_is_created_once_and_read_with_its_marker(repo, tmp_path):
    """The claim's push is create-only: a second claim, a fast-forward a plain push would take,
    is ClaimTaken; the claim read back names its tip, when it was committed and when its
    marker says it was claimed.  Its commit adds the marker and nothing else."""
    base = _origin(tmp_path, repo)
    sha = _git.claim(repo, 12, _git.tracking(_git.DEV), _MON)
    assert _run(repo, "diff", "--name-status", base, sha) == "A\tplan/ships/12"
    assert _run(repo, "cat-file", "blob", f"{sha}:plan/ships/12") == "claimed 2026-10-19T13:00:00Z"
    assert _git.on_origin(repo) == {12: sha}
    with pytest.raises(_git.ClaimTaken):
        _git.claim(repo, 12, sha, _TUE)
    reads = _git.read(repo)
    claim = reads.claims[12]
    assert (claim.sha, claim.claimed, claim.branch) == (sha, "2026-10-19T13:00:00Z", "card/12")
    assert datetime.fromisoformat(claim.committed)
    assert reads.shipped.cards == frozenset()


def test_the_same_claim_pushed_again_is_the_claim_made(repo, tmp_path, monkeypatch):
    """Session 8's row 2: git answers a push of the very commit the branch holds as up to date,
    exit 0, without testing the lease.  A claim repeated after a lost answer writes that same
    commit (same base, marker, author and committer instants), so it is the claim made."""
    _origin(tmp_path, repo)
    for variable in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(variable, "2026-10-19T13:00:00+00:00")
    first = _git.claim(repo, 3, _git.tracking(_git.DEV), _MON)
    assert _git.claim(repo, 3, _git.tracking(_git.DEV), _MON) == first


def test_only_a_card_number_under_card_is_a_claim_and_merged_work_is_none(repo, tmp_path):
    """``card/x`` and ``card/007`` name no card; a ``card/N`` whose tip ``dev`` or ``main``
    holds is merged work whose branch outlived its merge, not a claim."""
    base = _origin(tmp_path, repo)
    for branch in ("card/x", "card/007", "feat/y"):
        _push(repo, base, branch)
    merged = _git.claim(repo, 8, _git.tracking(_git.DEV), _MON)
    _push(repo, merged, "dev")
    hotfix = _git.claim(repo, 9, _git.tracking(_git.MAIN), _MON)
    _push(repo, hotfix, "main")
    live = _git.claim(repo, 10, _git.tracking(_git.DEV), _MON)
    assert _git.on_origin(repo) == {8: merged, 9: hotfix, 10: live}
    assert list(_git.read(repo).claims) == [10]


def test_a_claim_tip_without_its_marker_reads_no_claim_instant(repo, tmp_path):
    """A ``card/N`` someone pushed by hand, holding no marker for N, is still a claim (origin
    holds the branch), with no instant to report."""
    base = _origin(tmp_path, repo)
    _push(repo, _files(repo, base, "by hand", {"work": "x\n"}), "card/4")
    assert _git.read(repo).claims[4].claimed is None


def test_check_out_writes_no_upstream(repo, tmp_path):
    """The claim's local branch tracks nothing, so the shared configuration gains no
    ``branch.card/N`` section."""
    _origin(tmp_path, repo)
    sha = _git.claim(repo, 5, _git.tracking(_git.DEV), _MON)
    assert _git.check_out(repo, 5, sha) is None
    assert _run(repo, "symbolic-ref", "--short", "HEAD") == "card/5"
    assert _git.has_branch(repo, "card/5") and not _git.has_branch(repo, "card/6")
    assert "branch.card/5" not in _run(repo, "config", "--list")


def test_a_release_keeps_the_work_and_deletes_only_the_tip_it_read(repo, tmp_path):
    """The work moves to ``released/N/<instant>`` first; the delete names the tip read, so a
    newer push survives a release read before it, and is named."""
    _origin(tmp_path, repo)
    sha = _git.claim(repo, 6, _git.tracking(_git.DEV), _MON)
    newer = _files(repo, sha, "more work", {"work": "x\n"})
    _push(repo, newer, "card/6")
    with pytest.raises(GitError, match="moved since it was read"):
        _git.release(repo, 6, sha, _MON)
    assert _git.on_origin(repo) == {6: newer}
    kept = _git.release(repo, 6, newer, _TUE)
    assert kept == "released/6/20261020T093000Z"
    assert not _git.on_origin(repo)
    assert _run(repo, "ls-remote", "origin", f"refs/heads/{kept}").split("\t")[0] == newer


def test_history_lists_each_commit_that_touched_the_marker(repo, tmp_path):
    """``show``'s history: the claim's commit that added it, the revert that deleted it, newest
    first, on ``dev`` or ``main``."""
    _origin(tmp_path, repo)
    sha = _git.claim(repo, 2, _git.tracking(_git.DEV), _MON)
    _push(repo, sha, "dev")
    revert = _files(repo, sha, "Revert claim plan#2", {_git.marker(2): None})
    _push(repo, revert, "dev")
    _git.fetch(repo)
    assert _git.history(repo, 2) == [("D", revert, "Revert claim plan#2"),
                                     ("A", sha, "claim plan#2")]


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

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, tmp_path,
                                                                     monkeypatch):
    """The incident's mechanism replayed onto a sentinel: every write the tests' builder makes
    and every git call this module makes -- the fetch, the record's merge, the claim's objects,
    push and branch, the release's two pushes -- lands in the directory named, every answer
    is that directory's, and the sentinel -- its refs, config, HEADs and indexes -- is
    byte-identical afterwards.  The sentinel is proven bound, and its indexes not empty
    (review cp3 M-1's control), before it is handed over (``scratch._prove_bound``).  The
    trailer rules' own calls, ``is_ancestor`` among them, are held the same way in
    ``tools/ci/test_trailers.py``."""
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)
    work = scratch.repository(tmp_path, "work")
    _origin(tmp_path, work)
    sha = _git.claim(work, 3, _git.tracking(_git.DEV), _MON)
    assert _git.check_out(work, 3, sha) is None
    reads = _git.read(work)
    assert (list(reads.claims), reads.shipped.cards) == ([3], frozenset())
    _push(work, sha, "dev")
    _git.fetch(work)
    assert _git.shipped(work).cards == {3}
    assert _git.history(work, 3) == [("A", sha, "claim plan#3")]
    assert _git.author_date(work, sha)
    assert _git.release(work, 3, sha, _TUE).startswith("released/3/")
    assert _git.has_branch(work, "card/3")
    assert _git.current_branch(work) == "card/3"
    assert _git.pushed(work, "dev") and not _git.pushed(work, "card/3")

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
