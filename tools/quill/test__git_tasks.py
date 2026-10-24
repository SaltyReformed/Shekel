"""The tracker tool's git reading of a task's done file (ruling ``balance:R-BAL386``), from
throwaway repositories under pytest's tmp_path.

A task is done when its file ``plan/tasks/T`` is in the merge of ``dev`` and ``main``, or in
the tip of its card's live claim, read as a marker is read (:func:`_git.lines_in`).  Which card
a task is under only the tracker says, so git's answer for a claim names every task file its
tip holds; ``quill sync`` keeps those of tasks under that claim's card.  The tracking refs are
pointed directly (:func:`scratch.point_dev`, :func:`scratch.point_main`), as the readers here
take them as they are and fetch nothing.
"""
from __future__ import annotations

import pytest

from tools.ci import scratch
from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill import _git


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository under tmp_path whose ``origin/dev`` and ``origin/main`` hold one base
    commit; the repository and the base."""
    root = scratch.repository(tmp_path)
    base = commit_files(root, scratch.commit(root, "root"), "base", {"app.py": "base\n"})
    point_dev(root, base)
    point_main(root, base)
    return root, base


def _done(number, when="2026-10-23T22:00:00Z"):
    """``{plan/tasks/<number>: its line}``, a task's file as the commit finishing it writes it."""
    return {_git.task_file(number): f"done {when}\n"}


def test_a_tasks_file_is_named_as_a_marker_is():
    """A task's file is ``plan/tasks/<number>``, read by the markers' own predicate: a name
    that is no number, a file below the directory, or the directory itself names no task; and
    neither directory's file names the other's."""
    assert _git.task_file(12) == "plan/tasks/12"
    assert _git.task_of("plan/tasks/12") == 12
    for path in ("plan/tasks/notes", "plan/tasks/012", "plan/tasks/1/inner", "plan/tasks",
                 "plan/ships/12"):
        assert _git.task_of(path) is None, path
    assert _git.card_of("plan/tasks/12") is None


def test_what_is_done_is_the_merge_of_dev_and_main(code):
    """A task done on dev counts, and so does one done by a hotfix on main before main is
    merged into dev; one main deleted counts no longer; an entry under ``plan/tasks/`` that
    is no task's file is a HISTORY line, never a task; and the cards are read as before."""
    root, base = code
    both = commit_files(root, base, "card 8 with tasks 21 and 23",
                        {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n", **_done(21),
                         **_done(23)})
    point_dev(root, commit_files(root, both, "a stray", {"plan/tasks/notes": "x\n"}))
    point_main(root, commit_files(root, both, "task 22 done, task 23 undone",
                                  {**_done(22), _git.task_file(23): None}))
    found = _git.shipped(root)
    assert (found.cards, found.tasks) == ({8}, {21, 22})
    assert found.history == ("plan/tasks/notes on origin/dev or origin/main is no task's file "
                             "(a task's file is a file plan/tasks/<task number>): it is not "
                             "acted on",)


def test_a_revert_of_the_commit_finishing_a_task_makes_it_not_done(code):
    """R-BAL386's "a revert reopens it": card 8 lands with task 30's file written by the
    commit finishing it; ``git revert`` of that commit deletes the file with its work, so the
    task is no longer done, and the card stays shipped."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n"})
    piece = commit_files(root, claimed, "task 30's piece", {"w30.txt": "work\n", **_done(30)})
    landed = run(root, "commit-tree", run(root, "merge-tree", "--write-tree", base, piece),
                 "-p", base, "-p", piece, "-m", "Merge card/8")
    point_dev(root, landed)
    assert _git.shipped(root).tasks == {30}
    run(root, "checkout", "--quiet", "--detach", landed)
    run(root, "revert", "--no-edit", piece)
    point_dev(root, run(root, "rev-parse", "HEAD"))
    assert (_git.shipped(root).cards, _git.shipped(root).tasks) == ({8}, frozenset())


def test_a_tasks_file_that_conflicts_refuses_quills_read_but_not_the_markers_readers(code):
    """dev and main each write task 31's file anew from the same line: their merge holds
    git's conflict text there, so whether it is done is unknown and quill's read refuses,
    naming it.  The markers' readers (CI's landing check, the release tool's
    ``merged_tree``) see no conflict: the release tool never reads a task."""
    root, base = code
    both = commit_files(root, base, "task 31", _done(31))
    point_dev(root, dev := commit_files(root, both, "dev", _done(31, "2026-10-24T08:00:00Z")))
    point_main(root, main := commit_files(root, both, "main", _done(31, "2026-10-24T09:00:00Z")))
    merged = _git.merge(root, (dev, main))
    assert (merged.conflicts, merged.task_conflicts) == ((), ("plan/tasks/31",))
    assert _git.merged_tree(root, (dev, main)) == merged.tree
    with pytest.raises(_git.MarkerConflict, match=(
            "merge with a conflict on plan/tasks/31, so whether those cards shipped or those "
            "tasks are done is unknown")) as raised:
        _git.shipped(root)
    assert raised.value.paths == ("plan/tasks/31",)


def test_a_claims_tip_names_every_task_file_it_holds(code):
    """A live claim's tip names each task whose file it holds, whatever card the tracker says
    each is under (sync keeps those under the claim's card); a stray there names none; a
    claim with no task file names none."""
    root, base = code
    claimed = commit_files(root, base, "claim plan#8",
                           {_git.marker(8): "claimed 2026-10-19T13:00:00Z\n"})
    tip = commit_files(root, claimed, "two pieces and a stray",
                       {**_done(40), **_done(41), "plan/tasks/x/y": "x\n"})
    other = commit_files(root, base, "claim plan#9",
                         {_git.marker(9): "claimed 2026-10-19T13:00:00Z\n"})
    for number, sha in ((8, tip), (9, other)):
        run(root, "update-ref", f"refs/remotes/origin/{_git.card_branch(number)}", sha)
    found = _git.claims(root, [8, 9])
    assert {number: claim.tasks for number, claim in found.items()} == {
        8: {40, 41}, 9: frozenset()}
