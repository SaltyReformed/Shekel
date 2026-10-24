"""The git histories CI's landing check is tested over, and the one way its tests judge one.

Shared by ``test_landing.py`` (rules 1-7) and ``test_landing_done.py`` (rule 8), so each
history is spelled once.  Every history is a throwaway repository's (:mod:`tools.ci.scratch`),
its markers written as files and its merges REAL ones (:func:`merge`: git's three-way merge,
two parents), judged as CI judges one: the merge of the pull request's head into its base's
tip (:func:`judging`).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.ci.scratch import commit, commit_files, point_dev, point_main, repository, run
from tools.quill._git import marker
from tools.quill.landing import PullRequest, main

#: When the tests' claims are made, and when a later one is.
MON, TUE = "2026-10-19T13:00:00Z", "2026-10-20T09:30:00Z"


def never():
    """A connection no test may make: the tracker is read only when a card must be."""
    raise AssertionError("the tracker was read")


def start(parent):
    """A repository under ``parent`` whose ``origin/dev`` and ``origin/main`` hold one base
    commit (its ``app.py``), the start of every history; the repository and the base."""
    root = repository(parent)
    base = commit_files(root, commit(root, "root"), "base", {"app.py": "base\n"})
    point_dev(root, base)
    point_main(root, base)
    return root, base


def merge(root, first, second, message="merge"):
    """``second`` merged into ``first`` as git's three-way merge writes it: a commit of the
    two, ``first`` its first parent; its sha."""
    tree = run(root, "merge-tree", "--write-tree", first, second)
    return run(root, "commit-tree", tree, "-p", first, "-p", second, "-m", message)


def claim(root, on, number, when=MON):
    """Card ``number``'s branch as ``quill claim`` starts it on ``on`` and its session builds
    it: the claim commit adding the marker ``claimed <when>``, then a commit of its work; the
    claim commit and the branch's tip."""
    claimed = commit_files(root, on, f"claim plan#{number}", {marker(number): f"claimed {when}\n"})
    return claimed, commit_files(root, claimed, f"work {number}", {f"w{number}.txt": "work\n"})


def landed(root, onto, number, when=MON):
    """Card ``number`` claimed on ``onto`` and its branch merged into it, as its queue lands
    it; the merge."""
    return merge(root, onto, claim(root, onto, number, when)[1], f"Merge card/{number}")


def undone(root, on, number):
    """A revert on ``on`` of card ``number``'s landing: its marker and work deleted."""
    return commit_files(root, on, f"Revert plan#{number}",
                        {marker(number): None, f"w{number}.txt": None})


def pull(head, sha, base="dev"):
    """A read of the pull request of branch ``head`` (None: another repository's) at ``sha``
    into ``base``, as :func:`landing.pull_request` makes one."""
    return lambda number: PullRequest(number, base, head, sha)


def judging(capsys, root):
    """The check run over the merge-queue group of pull request #600 in ``root``, as a test
    calls it: with ``onto``, the tip of ``base``, branch ``head`` at ``head_sha``, and
    ``connect``, which makes the tracker's reader, it judges GitHub's merge of the two and
    answers its exit status, output and error, as pytest's ``capsys`` captured them."""
    def _judge(onto, head_sha, head, base, connect):
        """Judge pull request #600 of ``head`` into ``base`` (:func:`judging`)."""
        (point_dev if base == "dev" else point_main)(root, onto)
        merged = merge(root, onto, head_sha, f"Merge pull request #600 from {head}")
        status = main(["merge_group", merged,
                       f"refs/heads/gh-readonly-queue/{base}/pr-600-{onto}"],
                      connect, pull(head, head_sha, base), root)
        out = capsys.readouterr()
        return status, out.out, out.err

    return _judge


def verdict(head, changes, refused, base="dev"):
    """The check's last line for pull request #600 of branch ``head`` into ``base``."""
    return (f"landing check: pull request #600 into {base} from {head}: {changes} ship marker "
            f"change(s), {refused} refused")
