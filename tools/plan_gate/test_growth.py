"""Controls for the registries' GROWTH BOUND (:mod:`_growth`, balance:R-BAL128).

The bound replaced three absolute "runaway backstops" (600 rulings, 400 steps,
400 findings) whose controls lowered the constant to 1 and watched the arm
fire.  That shape cannot grade this arm: what it measures is a CHANGE, so each
control here builds the change -- a working tree, a commit, a merge -- in a
SCRATCH repository and grades it, for every bounded registry.  The builder is
``test_shipped_against_git``'s, pinned by its ``_isolate`` to the scratch, so
no control can touch the developer's repository or read his git config.

**Each arm is graded where it is the only grader.**  The working-tree arm is
what the pre-commit hook sees and is zero in CI, where the tree IS ``HEAD``;
the commit arm is what CI sees, and it walks THROUGH merges to the commits they
bring.  So each has its firing pair (101 fires, 100 passes).  A MERGE is graded
by the rows it adds OF ITS OWN, and its controls say both halves: a merge
bringing 150 rows through its parents passes, one writing 101 of its own
fires -- committed and in progress -- and a merge bringing a single commit of
101 does not launder it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import _growth as growth
import _registry as registry
import _rulings as rulings
from test_shipped_against_git import _IDENT, _git, _isolate

#: Every bounded registry, so every control runs once per file.
REGISTRIES = sorted(growth.REGISTRIES)

#: The rows each scratch registry starts with: growth is measured against a
#: table that already exists, and CREATING one has a control of its own.
BASE = 10


def _document(header: tuple[str, ...], rows: int, tag: str = "r") -> str:
    """Return a registry document: one table under *header* holding *rows* rows.

    Args:
        header: The table's header cells.
        rows: How many body rows to write.
        tag: Prefixes every cell, so two documents can hold different rows.

    Returns:
        The whole document, shaped as the live registries are.
    """
    width = len(header)
    body = [
        "| " + " | ".join(f"{tag}{number}" for _ in range(width)) + " |"
        for number in range(rows)
    ]
    lines = ["# scratch registry", "", "| " + " | ".join(header) + " |",
             "|" + "---|" * width, *body]
    return "\n".join(lines) + "\n"


def _write(root: Path, rel: str, rows: int, **shape) -> None:
    """Write *rel* in the scratch with *rows* rows, under its header unless told otherwise.

    Args:
        root: The scratch repository.
        rel: The registry's path from the root.
        rows: How many body rows.
        **shape: ``header`` to write under another header, ``tag`` to vary
            the rows' content.
    """
    header = shape.get("header", growth.REGISTRIES[rel])
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_document(header, rows, shape.get("tag", "r")))


def _commit(root: Path, message: str) -> str:
    """Commit everything in the scratch and return the new commit's sha."""
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _side_branch(root: Path, rel: str, batches: list[int]) -> list[str]:
    """Commit *batches* of rows to *rel* on a branch ``side``, then return to ``main``.

    ``main`` then takes one commit of its own that touches no registry, so a
    merge of ``side`` is a true merge rather than a fast-forward.

    Args:
        root: The scratch repository.
        rel: The registry the side grows.
        batches: Rows each side commit adds, in order.

    Returns:
        The side's commits, oldest first.
    """
    _git(root, "checkout", "-q", "-b", "side")
    shas, rows = [], BASE
    for number, batch in enumerate(batches):
        rows += batch
        _write(root, rel, rows)
        shas.append(_commit(root, f"side commit {number}: {batch} rows"))
    _git(root, "checkout", "-q", "main")
    (root / "unrelated.txt").write_text("main moves on\n")
    _commit(root, "main moves on, touching no registry")
    return shas


@pytest.fixture(name="scratch")
def _scratch(tmp_path, monkeypatch) -> Path:
    """A scratch repository holding every registry at :data:`BASE` rows, graded by the gate."""
    _isolate(monkeypatch, tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    for rel in growth.REGISTRIES:
        _write(tmp_path, rel, BASE)
    _commit(tmp_path, "the base")
    monkeypatch.setattr(registry, "REPO", tmp_path)
    return tmp_path


class TestTheLiveTree:
    """The live checkout is within the bound, and the arms read what they claim."""

    def test_the_bound_is_the_developers_number(self):
        """The ruling, asserted: raising it is a question, not an edit (rule 4)."""
        assert growth.GROWTH_BOUND == 100

    def test_the_three_registries_are_bounded(self):
        """A registry dropped from the map would be bounded by nothing."""
        assert set(growth.REGISTRIES) == {
            "docs/plans/rulings.md", "docs/plans/steps.md", "docs/plans/ledger.md",
        }

    def test_the_count_is_each_producers_own(self):
        """Counted exactly as each registry's producer counts, on the live files.

        The equality is what makes zero violations mean zero: an arm that read
        no table would count 0 rows on both sides of every change and pass.
        """
        producers = {
            "docs/plans/rulings.md": rulings.ruling_rows,
            "docs/plans/steps.md": registry.step_rows,
            "docs/plans/ledger.md": registry.ledger_rows,
        }
        for rel, header in growth.REGISTRIES.items():
            text = (registry.REPO / rel).read_text()
            counted = growth.net_rows_added(None, text, header)
            assert counted == len(producers[rel]()) > 0, rel

    def test_the_working_tree_is_within_the_bound(self):
        """Arm (a) on the live tree: what the pre-commit hook grades."""
        assert not growth.working_tree_growth_violations()

    def test_every_commit_head_brings_is_within_the_bound(self):
        """Arm (b) on the live tree: what CI grades, on its test merge."""
        assert not growth.commit_growth_violations()


@pytest.mark.parametrize("rel", REGISTRIES)
class TestTheWorkingTreeArm:
    """Arm (a): the working tree against HEAD, which is the commit about to be made."""

    def test_one_row_past_the_bound_fires(self, scratch, rel):
        """101 rows added, uncommitted: refused, naming the file and the count."""
        _write(scratch, rel, BASE + 101)
        problems = growth.working_tree_growth_violations()
        assert len(problems) == 1, problems
        assert rel in problems[0] and "adds 101 rows" in problems[0]
        assert "R-BAL128" in problems[0]

    def test_the_bound_itself_passes(self, scratch, rel):
        """100 rows added is within the bound."""
        _write(scratch, rel, BASE + 100)
        assert not growth.working_tree_growth_violations()

    def test_a_merge_being_committed_is_graded_by_its_own_rows(self, scratch, rel):
        """In progress: 150 rows its side brought pass, 101 more of its own fire.

        ``--no-commit`` stops the merge exactly where the pre-commit hook runs.
        Three states, one ``MERGE_HEAD`` apart or one edit apart: the merge as
        git made it (its own rows 0), the same merge with 101 rows written
        into it (a script resolving a conflict), and the 150-row tree written
        by hand with no merge in progress, which is plain growth and fires.
        """
        _side_branch(scratch, rel, [75, 75])
        _git(scratch, "merge", "-q", "--no-commit", "side")
        assert len((scratch / rel).read_text().splitlines()) == 4 + BASE + 150
        assert not growth.working_tree_growth_violations()

        _write(scratch, rel, BASE + 150 + 101)
        problems = growth.working_tree_growth_violations()
        assert len(problems) == 1, problems
        assert "The merge being committed" in problems[0]
        assert "adds 101 rows of its own" in problems[0] and rel in problems[0]

        _write(scratch, rel, BASE + 150 + 100)
        assert not growth.working_tree_growth_violations()

        _git(scratch, "checkout", "-q", "--", rel)
        _git(scratch, "merge", "--abort")
        _write(scratch, rel, BASE + 150)
        problems = growth.working_tree_growth_violations()
        assert len(problems) == 1 and "adds 150 rows" in problems[0], problems


@pytest.mark.parametrize("rel", REGISTRIES)
class TestTheCommitArm:
    """Arm (b): every commit HEAD brings; a non-merge against its parent (merges: below)."""

    def test_a_commit_one_row_past_the_bound_fires(self, scratch, rel):
        """Committed, the working-tree arm is blind to it and this arm is not."""
        _write(scratch, rel, BASE + 101)
        sha = _commit(scratch, "a runaway batch")
        assert not growth.working_tree_growth_violations()
        problems = growth.commit_growth_violations()
        assert len(problems) == 1, problems
        assert sha[:9] in problems[0] and rel in problems[0]
        assert "adds 101 rows" in problems[0]

    def test_a_commit_at_the_bound_passes(self, scratch, rel):
        """100 rows in one commit is within the bound, and the commit WAS graded."""
        _write(scratch, rel, BASE + 100)
        sha = _commit(scratch, "a real batch")
        assert growth.graded_commits() == [(sha, (_git(scratch, "rev-parse", "HEAD^"),), [rel])]
        assert not growth.commit_growth_violations()

    def test_a_merge_bringing_150_rows_passes(self, scratch, rel):
        """The side's two commits are graded for the rows; the merge, for none of its own.

        The premise is asserted before the verdict: the merge really does add
        150 rows against its first parent, and the range really does hold the
        side's two commits AND the merge -- otherwise a pass could mean the
        arm read nothing.
        """
        side = _side_branch(scratch, rel, [75, 75])
        _git(scratch, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        merge = _git(scratch, "rev-parse", "HEAD")
        header = growth.REGISTRIES[rel]
        before = _git(scratch, "show", f"HEAD^1:{rel}")
        after = (scratch / rel).read_text()
        assert growth.net_rows_added(before, after, header) == 150
        graded = {sha: parents for sha, parents, _ in growth.graded_commits()}
        assert sorted(graded) == sorted([*side, merge])
        assert len(graded[merge]) == 2
        assert not growth.commit_growth_violations()

    def test_a_merge_does_not_launder_a_commit_past_the_bound(self, scratch, rel):
        """A single side commit of 101 is refused when a merge brings it."""
        runaway = _side_branch(scratch, rel, [101])[0]
        _git(scratch, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and runaway[:9] in problems[0], problems

    def test_a_merge_that_nets_zero_is_still_walked(self, scratch, rel):
        """A side that added 101 rows and removed them again is still graded.

        The merge leaves the registry as its first parent had it, and git's
        default history simplification then follows the first parent ALONE
        and never lists the side: this is the case ``--full-history`` exists
        for, and a pass here without it would be the arm reading nothing.
        """
        runaway = _side_branch(scratch, rel, [101, -101])[0]
        _git(scratch, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        assert _git(scratch, "diff", "HEAD^1", "HEAD", "--", rel) == ""
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and runaway[:9] in problems[0], problems

    def test_rows_are_counted_net(self, scratch, rel):
        """A change that drops 60 rows and writes 150 new ones adds 90.

        Gross, 150 rows appeared and the bound would fire; net, the registry
        grew by 90.  A row removed and re-added is never counted twice.
        """
        _write(scratch, rel, BASE + 50)
        _commit(scratch, "fifty rows")
        _write(scratch, rel, 150, tag="n")
        _commit(scratch, "every row rewritten, ninety more")
        assert not growth.commit_growth_violations()

    def test_creating_a_registry_counts_every_row(self, scratch, rel):
        """A revision without the file holds none, so creation counts in full."""
        (scratch / rel).unlink()
        _commit(scratch, "the registry is removed")
        assert not growth.commit_growth_violations()
        _write(scratch, rel, 101)
        sha = _commit(scratch, "the registry is created with 101 rows")
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and sha[:9] in problems[0], problems

    def test_a_header_change_is_measured_by_table_lines(self, scratch, rel):
        """A table under a retired header is counted by its lines, not skipped.

        ``steps.md``'s header changed once (``32193c16``); every revision
        before it is unreadable by today's producer.  Moving 160 rows onto the
        current header adds none; a header change that also adds 101 does.
        """
        retired = growth.REGISTRIES[rel][:-1]
        _write(scratch, rel, 160, header=retired)
        _commit(scratch, "160 rows under a retired header")
        _write(scratch, rel, 160)
        _commit(scratch, "the header changes, no row is added")
        assert not growth.commit_growth_violations()
        _write(scratch, rel, 261, header=retired)
        sha = _commit(scratch, "the header changes back, 101 rows added")
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and sha[:9] in problems[0], problems
        assert "adds 101 rows" in problems[0]

    def test_a_row_the_producer_refuses_is_still_counted(self, scratch, rel):
        """A runaway batch holding one mis-split row is counted, not skipped."""
        path = scratch / rel
        _write(scratch, rel, BASE + 101)
        path.write_text(path.read_text().replace("| r3 |", "| r3 | extra |", 1))
        _commit(scratch, "a runaway batch with a broken row")
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and "adds 101 rows" in problems[0], problems


def _other(rel: str) -> str:
    """Return a bounded registry that is not *rel*."""
    return next(other for other in REGISTRIES if other != rel)


def _merges(problems: list[str]) -> list[str]:
    """Return the messages that name a MERGE commit, not a commit it brought."""
    return [problem for problem in problems if problem.startswith("merge ")]


@pytest.mark.parametrize("rel", REGISTRIES)
class TestAMergeIsGradedByItsOwnRows:
    """A merge's own rows: its result less both parents' plus their merge base's."""

    def _merge_with_own_rows(self, scratch: Path, rel: str, own: int) -> str:
        """Merge ``side`` (75 + 75 rows) with *own* more written into the merge; return its sha."""
        _side_branch(scratch, rel, [75, 75])
        _git(scratch, "merge", "-q", "--no-commit", "side")
        _write(scratch, rel, BASE + 150 + own)
        return _commit(scratch, f"a merge writing {own} rows of its own")

    def test_an_evil_merge_adding_101_of_its_own_fires(self, scratch, rel):
        """Rows a script writes while resolving a merge are the ruling's runaway."""
        merge = self._merge_with_own_rows(scratch, rel, 101)
        problems = growth.commit_growth_violations()
        assert len(problems) == 1, problems
        assert problems[0].startswith(f"merge {merge[:9]}") and rel in problems[0]
        assert "adds 101 rows of its own" in problems[0]

    def test_a_merge_adding_100_of_its_own_passes(self, scratch, rel):
        """The bound applies to a merge's own rows exactly as to a commit's."""
        self._merge_with_own_rows(scratch, rel, 100)
        assert not growth.commit_growth_violations()

    def test_a_change_both_sides_made_alike_is_not_the_merges(self, scratch, rel):
        """Both sides drop the same 150 rows: the merge equals both parents and adds nothing.

        The formula alone reads ``10 - 10 - 10 + 160 = 150``, counting the one
        removal twice; the merge's document is IDENTICAL to both parents', so
        it added nothing of its own.
        """
        _write(scratch, rel, BASE + 150)
        _commit(scratch, "150 rows, before the fork")
        _git(scratch, "checkout", "-q", "-b", "side")
        _write(scratch, rel, BASE)
        _commit(scratch, "the side drops them")
        _git(scratch, "checkout", "-q", "main")
        _write(scratch, rel, BASE)
        _write(scratch, _other(rel), BASE + 5)
        _commit(scratch, "main drops the same rows, and files five elsewhere")
        _git(scratch, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        assert any(len(parents) == 2 for _, parents, _ in growth.graded_commits())
        assert not growth.commit_growth_violations()

    def test_a_merge_with_no_common_ancestor_is_graded_against_its_first_parent(
        self, scratch, rel,
    ):
        """No merge base: every row the unrelated side brings counts as the merge's own.

        The side is an ORPHAN holding 150 rows, so its root commit fires too;
        the control reads the merge's own verdict.  The formula with an EMPTY
        base would read ``160 - 10 - 150 + 0 = 0`` and pass it.
        """
        _git(scratch, "checkout", "-q", "--orphan", "stranger")
        _write(scratch, rel, 150, tag="s")
        _commit(scratch, "an unrelated history")
        _git(scratch, "checkout", "-q", "main")
        subprocess.run(
            ("git", *_IDENT, "merge", "-q", "--no-commit", "--allow-unrelated-histories",
             "stranger"),
            cwd=scratch, capture_output=True, text=True, check=False,
        )
        path = scratch / rel
        path.write_text(
            _document(growth.REGISTRIES[rel], BASE)
            + "".join(_document(growth.REGISTRIES[rel], 150, "s").splitlines(True)[4:]),
        )
        merge = _commit(scratch, "the unrelated history merged, both tables kept")
        mine = _merges(growth.commit_growth_violations())
        assert len(mine) == 1 and mine[0].startswith(f"merge {merge[:9]}"), mine
        assert "adds 150 rows of its own" in mine[0]

    def test_an_octopus_merge_counts_its_later_heads_as_its_own(self, scratch, rel):
        """Graded against its first two parents, so a third head's rows are its own."""
        other = _other(rel)
        _git(scratch, "checkout", "-q", "-b", "one")
        _write(scratch, rel, BASE + 20)
        _commit(scratch, "20 rows on the first head")
        _git(scratch, "checkout", "-q", "main")
        _git(scratch, "checkout", "-q", "-b", "two")
        _write(scratch, other, BASE + 101)
        _commit(scratch, "101 rows on the second head")
        _git(scratch, "checkout", "-q", "main")
        _git(scratch, "merge", "-q", "--no-ff", "-m", "octopus", "one", "two")
        octopus = _git(scratch, "rev-parse", "HEAD")
        mine = _merges(growth.commit_growth_violations())
        assert len(mine) == 1 and mine[0].startswith(f"merge {octopus[:9]}"), mine
        assert other in mine[0] and "adds 101 rows of its own" in mine[0]


class TestOneChangeTouchingSeveralRegistries:
    """A tick edits all three registries in one commit, so every one must be graded.

    Git lists the touched paths in path order (``ledger.md`` first), so an arm
    that graded only the first path, or stopped at the first violation, would
    never reach ``rulings.md`` -- the registry whose total prompted the ruling.
    """

    def test_only_the_registry_past_the_bound_is_named(self, scratch):
        """+5, +5 and +101 in one change: one violation, naming the last path."""
        _write(scratch, "docs/plans/ledger.md", BASE + 5)
        _write(scratch, "docs/plans/rulings.md", BASE + 5)
        _write(scratch, "docs/plans/steps.md", BASE + 101)
        problems = growth.working_tree_growth_violations()
        assert len(problems) == 1 and "docs/plans/steps.md" in problems[0], problems
        _commit(scratch, "a tick touching all three")
        problems = growth.commit_growth_violations()
        assert len(problems) == 1 and "docs/plans/steps.md" in problems[0], problems

    def test_every_registry_past_the_bound_is_named(self, scratch):
        """Two registries over in one change: two violations, one per file."""
        _write(scratch, "docs/plans/ledger.md", BASE + 101)
        _write(scratch, "docs/plans/rulings.md", BASE + 150)
        _write(scratch, "docs/plans/steps.md", BASE + 5)
        assert len(growth.working_tree_growth_violations()) == 2
        _commit(scratch, "two runaway batches in one commit")
        problems = growth.commit_growth_violations()
        assert len(problems) == 2, problems
        assert "docs/plans/ledger.md" in problems[0] and "adds 101 rows" in problems[0]
        assert "docs/plans/rulings.md" in problems[1] and "adds 150 rows" in problems[1]


@pytest.mark.parametrize("rel", REGISTRIES)
def test_a_root_commit_is_graded_whole(tmp_path, monkeypatch, rel):
    """``HEAD`` with no parent is the whole range, and it holds every row it creates.

    Never CI's case -- its ``HEAD`` is always a merge -- but the clause is
    stated in :mod:`_growth`, so it is graded rather than assumed.
    """
    _isolate(monkeypatch, tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    for other in growth.REGISTRIES:
        _write(tmp_path, other, 101 if other == rel else BASE)
    sha = _commit(tmp_path, "the first commit")
    monkeypatch.setattr(registry, "REPO", tmp_path)
    assert [commit for commit, parents, _ in growth.graded_commits() if not parents] == [sha]
    problems = growth.commit_growth_violations()
    assert len(problems) == 1 and sha[:9] in problems[0] and rel in problems[0], problems
