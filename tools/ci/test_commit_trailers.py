"""Controls for ``commit_trailers``: CI refuses a commit that breaks the card-trailer rules.

Every revert below is made by ``git revert`` itself in a throwaway repository,
so the records parsed are the ones git writes, not ones written to fit the
parser; the hand-written messages are only the shapes git does not make.
"""
from __future__ import annotations

import pytest

from tools.ci.commit_trailers import main, problems, reverts
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import run as _run


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """A repository with a work tree under tmp_path, one commit on ``dev``; not a real checkout."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=dev")
    _change(root, "base", "base")
    return root


def _change(root, name, *paragraphs):
    """Write file ``name`` and commit it with ``paragraphs`` as the message; the commit's sha."""
    (root / name).write_text(f"{name}\n", encoding="utf-8")
    _run(root, "add", name)
    _run(root, "commit", "--quiet", *[a for p in paragraphs for a in ("-m", p)])
    return _run(root, "rev-parse", "HEAD")


def _revert(root, sha, *options, trailer=None):
    """``git revert`` ``sha`` as git writes it, then add ``trailer`` if given; the new sha."""
    _run(root, "revert", "--no-edit", *options, sha)
    if trailer:
        _run(root, "commit", "--quiet", "--amend", "--no-edit", "--trailer", trailer)
    return _run(root, "rev-parse", "HEAD")


def _head(root):
    """The checked-out commit's full id."""
    return _run(root, "rev-parse", "HEAD")


class TestTheRevertRecordIsGitsOwn:
    """:func:`reverts` reads the records git writes, and nothing that merely mentions one."""

    def test_the_plain_record(self):
        """``This reverts commit <id>.``: the commit, no parent."""
        assert reverts("Revert \"x\"\n\nThis reverts commit " + "a" * 40 + ".\n") == [
            ("a" * 40, None)]

    def test_a_merge_record_names_the_parent_it_kept_on_the_next_line(self):
        """git wraps it: ``, reversing`` then ``changes made to <parent>.``."""
        message = f"This reverts commit {'a' * 40}, reversing\nchanges made to {'b' * 40}.\n"
        assert reverts(message) == [("a" * 40, "b" * 40)]

    def test_a_merge_record_rejoined_on_one_line_is_read_too(self):
        """An editor that re-wrapped the two lines into one changes nothing."""
        message = f"This reverts commit {'a' * 40}, reversing changes made to {'b' * 40}.\n"
        assert reverts(message) == [("a" * 40, "b" * 40)]

    def test_a_squash_of_several_reverts_holds_every_record(self):
        """Each record is its own undo; a parent belongs only to its own record."""
        message = (f"This reverts commit {'a' * 40}.\n\nThis reverts commit {'c' * 40}, "
                   f"reversing\nchanges made to {'d' * 40}.\n")
        assert reverts(message) == [("a" * 40, None), ("c" * 40, "d" * 40)]

    @pytest.mark.parametrize("message", [
        f"Why: the change that reverts commit {'a' * 40} broke the grid.\n",
        f"  This reverts commit {'a' * 40}.\n",
        f"> This reverts commit {'a' * 40}.\n",
        "This reverts commit xyz.\n",
    ])
    def test_a_mention_is_not_a_record(self, message):
        """Mid-line, indented, quoted or not an id: prose, not git's record."""
        assert not reverts(message)


class TestACleanChangeSetPasses:
    """Nothing to refuse: a green run, which still says how much it read."""

    def test_new_work_carrying_a_ship_passes(self, repo):
        """A leaf commit with ``Ships: plan#4`` and no revert."""
        base = _head(repo)
        _change(repo, "leaf", "leaf", "Ships: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_the_old_form_passes_until_the_cutover(self, repo):
        """``Ships: <arc>:<id>`` is the plan gate's until L8."""
        base = _head(repo)
        _change(repo, "leaf", "leaf", "Ships: balance:X-bk-1")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_a_revert_of_a_commit_citing_no_card_owes_nothing(self, repo):
        """The real revert already on dev (c56defbb8) is this shape."""
        work = _change(repo, "work", "feat: work")
        base = _head(repo)
        _revert(repo, work)
        assert problems(repo, base, _head(repo)) == (1, [])


class TestTheCitationShape:
    """Rule 1: a card is ``plan#<number>``; anything else is refused."""

    @pytest.mark.parametrize("value", ["plan #7", "Plan#8", "#9", "plan#09", "X-bk-1"])
    def test_a_value_naming_no_card_is_refused(self, repo, value):
        """The same values the tracker tool reports as malformed."""
        base = _head(repo)
        leaf = _change(repo, "leaf", "leaf", f"Ships: {value}")
        count, reasons = problems(repo, base, _head(repo))
        assert count == 1
        assert reasons == [f"{leaf[:12]} Ships: {value!r} is not plan#<number> "
                           "(nor the old <arc>:<id> form, read until the cutover)"]

    def test_a_malformed_trailer_before_the_change_set_is_not_graded(self, repo):
        """Only the change set's own commits: an old commit is not re-judged."""
        _change(repo, "old", "old", "Ships: plan #1")
        base = _head(repo)
        _change(repo, "leaf", "leaf", "Ships: plan#2")
        assert problems(repo, base, _head(repo)) == (1, [])


class TestARevertOwesTheOpposite:
    """Rule 2: a revert carries the opposite of every card trailer on what it undoes."""

    def test_undoing_a_ship_without_reopens_is_refused(self, repo):
        """The card's rule: git would keep calling plan#4 shipped."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        undo = _revert(repo, ship)
        count, reasons = problems(repo, base, _head(repo))
        assert count == 1
        assert reasons == [
            f"{undo[:12]} (Revert \"leaf\") undoes {ship[:12]} (leaf), which carries "
            f"`Ships: plan#4`; add the trailer `Reopens: plan#4` to {undo[:12]}'s message"]

    def test_undoing_a_ship_with_reopens_passes(self, repo):
        """The same revert, carrying ``Reopens: plan#4``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship, trailer="Reopens: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_a_reopens_for_another_card_does_not_pay_the_debt(self, repo):
        """``Reopens: plan#5`` undoes nothing of plan#4's."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship, trailer="Reopens: plan#5")
        assert len(problems(repo, base, _head(repo))[1]) == 1

    def test_undoing_a_reopen_without_ships_is_refused(self, repo):
        """Reverting a revert brings the code back: it owes ``Ships:`` (R-BAL207 decision).

        Git titles the re-landing ``Reapply "<subject>"`` and still writes its
        ``This reverts commit`` record, which is what is read.
        """
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        undo = _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        again = _revert(repo, undo)
        reasons = problems(repo, base, _head(repo))[1]
        assert reasons == [
            f"{again[:12]} (Reapply \"leaf\") undoes {undo[:12]} (Revert \"leaf\"), "
            f"which carries `Reopens: plan#4`; add the trailer `Ships: plan#4` to "
            f"{again[:12]}'s message"]

    def test_undoing_a_reopen_with_ships_passes(self, repo):
        """The re-landing carrying ``Ships: plan#4``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        undo = _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        _revert(repo, undo, trailer="Ships: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_a_shortened_reference_id_is_resolved(self, repo):
        """``git revert --reference`` writes ``<short id> (<subject>, <date>)``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship, "--reference")
        assert "This reverts commit " + ship[:7] in _run(repo, "log", "-1", "--format=%B")
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]


class TestRevertingAMerge:
    """The trailers sit on what the merge brought in, not on the merge."""

    @pytest.fixture(name="merged")
    def _merged(self, repo):
        """``dev`` with a merge of a branch whose leaf carries ``Ships: plan#4``; the merge."""
        _run(repo, "checkout", "--quiet", "-b", "feat")
        _change(repo, "leaf", "leaf", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        _change(repo, "other", "other")
        _run(repo, "merge", "--quiet", "--no-ff", "feat", "-m", "Merge feat")
        return _head(repo)

    def test_a_merge_revert_owes_reopens_for_what_the_merge_brought_in(self, repo, merged):
        """``git revert -m 1``: git names the kept parent; the leaf's Ships is owed."""
        undo = _revert(repo, merged, "-m", "1")
        reasons = problems(repo, merged, _head(repo))[1]
        assert len(reasons) == 1
        assert reasons[0].startswith(f"{undo[:12]} (Revert \"Merge feat\") undoes ")
        assert "`Ships: plan#4`; add the trailer `Reopens: plan#4`" in reasons[0]

    def test_a_merge_revert_carrying_reopens_passes(self, repo, merged):
        """The same revert, carrying the trailer."""
        _revert(repo, merged, "-m", "1", trailer="Reopens: plan#4")
        assert problems(repo, merged, _head(repo)) == (1, [])

    def test_a_merge_revert_that_lost_its_parent_line_is_refused(self, repo, merged):
        """Without the kept parent, what was undone cannot be read: refused, not passed."""
        undo = _change(repo, "gone", "Revert \"Merge feat\"", f"This reverts commit {merged}.")
        reasons = problems(repo, merged, _head(repo))[1]
        assert reasons == [
            f"{undo[:12]} (Revert \"Merge feat\"): it reverts merge {merged[:12]} without the "
            f"line naming the parent it kept (\"reversing changes made to <parent>\"), so "
            f"what it undid cannot be read"]

    def test_a_merge_revert_naming_a_parent_it_does_not_have_is_refused(self, repo, merged):
        """A kept parent that is not one of the merge's parents names nothing it undid."""
        stranger = _commit(repo, "stranger")
        _change(repo, "gone", "Revert", f"This reverts commit {merged}, reversing\n"
                                        f"changes made to {stranger}.")
        reasons = problems(repo, merged, _head(repo))[1]
        assert len(reasons) == 1
        assert f"it says it kept {stranger}, which is not a parent of merge" in reasons[0]


class TestWhatCannotBeReadIsRefused:
    """A check that cannot read its subject has not checked it."""

    def test_a_revert_of_a_commit_this_repository_does_not_hold_is_refused(self, repo):
        """A rebased-away or mistyped id."""
        base = _head(repo)
        undo = _change(repo, "gone", "Revert", f"This reverts commit {'e' * 40}.")
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert): it says it reverts commit {'e' * 40}, which this "
            f"repository does not hold"]

    def test_the_undone_commit_may_sit_before_the_change_set(self, repo):
        """Reverting an old commit reads that commit, wherever it is in history."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        _change(repo, "later", "later")
        base = _head(repo)
        _revert(repo, ship)
        assert len(problems(repo, base, _head(repo))[1]) == 1


class TestTheCommandLine:
    """What ``ci.yml``'s ``commit-trailers`` job runs."""

    def test_a_clean_change_set_exits_zero_and_says_what_it_read(self, repo, capsys):
        """A green run names its range and its commit count: it measured something."""
        base = _head(repo)
        _change(repo, "leaf", "leaf", "Ships: plan#4")
        assert main([base, _head(repo)], root=repo) == 0
        assert capsys.readouterr().out.endswith(
            f"commit trailers: 1 commit(s) in {base[:12]}..{_head(repo)[:12]}, 0 refused\n")

    def test_a_refusal_exits_one_and_prints_each_reason(self, repo, capsys):
        """Each reason on its own REFUSED line, then the count."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship)
        assert main([base, _head(repo)], root=repo) == 1
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith("REFUSED -- ") and out[-1].endswith(", 1 refused")

    @pytest.mark.parametrize("argv", [
        [],
        ["a" * 40],
        ["a" * 40, "b" * 39],
        ["a" * 40, "B" * 40],
        ["0" * 40, "b" * 40],
        ["a" * 64, "0" * 64],
        ["", ""],
    ])
    def test_anything_but_two_full_ids_is_a_usage_error(self, argv, capsys):
        """An empty or partial range from the workflow checks nothing, so it is not green."""
        assert main(argv) == 2
        assert "usage" in capsys.readouterr().err

    def test_a_range_git_cannot_read_exits_two(self, repo, capsys):
        """An id this repository does not hold is a failure, never an empty change set."""
        assert main(["e" * 40, _head(repo)], root=repo) == 2
        assert "git could not read" in capsys.readouterr().err
