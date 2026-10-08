"""Controls for ``commit_trailers``: CI refuses a commit that breaks the card-trailer rules.

Every revert below is made by ``git revert`` itself in a throwaway repository,
so the records parsed are the ones git writes, not ones written to fit the
parser; the hand-written messages are only the shapes git does not make.
"""
from __future__ import annotations

import pytest

from tools.ci.commit_trailers import main, problems, reverts
from tools.ci.scratch import commit as _commit
from tools.ci.scratch import repository
from tools.ci.scratch import run as _run
from tools.ci.trailers import history, shipped


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """A repository with a work tree under tmp_path, one commit on ``dev``; not a real checkout."""
    root = repository(tmp_path)
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


def _squash(root, base):
    """Squash every commit made on ``base`` into one holding their messages, oldest first, as
    a squash merge or an interactive rebase writes them; the new sha."""
    shas = _run(root, "rev-list", "--reverse", f"{base}..HEAD").split()
    messages = [_run(root, "log", "-1", "--format=%B", sha) for sha in shas]
    _run(root, "reset", "--quiet", "--soft", base)
    _run(root, "commit", "--quiet", *[arg for message in messages for arg in ("-m", message)])
    return _head(root)


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
        "This reverts commit added in the last release.\n",
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


class TestARevertKeepsGitsAnswerTrue:
    """Rule 2: a revert owes the ``Reopens:`` or ``Ships:`` that keeps git's answer true to
    the code it removes or brings back."""

    def test_undoing_a_ship_without_reopens_is_refused(self, repo):
        """The card's rule: git would keep calling plan#4 shipped."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        undo = _revert(repo, ship)
        count, reasons = problems(repo, base, _head(repo))
        assert count == 1
        assert reasons == [
            f"{undo[:12]} (Revert \"leaf\") undoes {ship[:12]} (leaf), which carries "
            f"`Ships: plan#4`; add the trailer `Reopens: plan#4` to the last paragraph of "
            f"{undo[:12]}'s message"]

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

    def test_the_wrong_trailer_does_not_pay_the_debt(self, repo):
        """``Ships: plan#4`` on the revert of a ship would keep plan#4 shipped: the
        ``Reopens:`` is still owed."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship, trailer="Ships: plan#4")
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]

    def test_the_revert_is_judged_without_its_own_trailers(self, repo):
        """Git's answer is asked where the revert lands, its first parent: counted, the
        ``Reopens: plan#4`` a re-landing wrongly carries would cancel the very ship it
        brings back, and the ``Ships:`` it owes would never be asked."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        undo = _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        _revert(repo, undo, trailer="Reopens: plan#4")
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Ships: plan#4`" in reasons[0]

    def test_undoing_a_reopen_without_ships_is_refused(self, repo):
        """Reverting a revert brings the code back: without the ``Reopens:`` git calls
        plan#4 shipped, so ``Ships:`` is owed.

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
            f"which carries `Reopens: plan#4`; add the trailer `Ships: plan#4` to the last "
            f"paragraph of {again[:12]}'s message"]

    def test_undoing_a_reopen_with_ships_passes(self, repo):
        """The re-landing carrying ``Ships: plan#4``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        undo = _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        _revert(repo, undo, trailer="Ships: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_undoing_a_stray_reopens_owes_nothing(self, repo):
        """Review of fe5953d84, HIGH 1(a): a mistyped ``Reopens: plan#9`` cancelled nothing.

        Git never called plan#9 shipped, before the stray or after it, so
        undoing it moves nothing.  Owing ``Ships: plan#9`` would make git call a
        card shipped that no commit built.
        """
        stray = _change(repo, "leaf", "leaf", "Reopens: plan#9")
        base = _head(repo)
        _revert(repo, stray)
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_undoing_a_ship_a_reopens_already_cancelled_still_owes_reopens(self, repo):
        """Review of the landing rule, MEDIUM 1 (residual ii): the undo leaves its trace.

        S0 ships plan#4 and B reopens it.  R0, reverting S0, moves no answer,
        but owes ``Reopens:`` all the same: without it, a later undo of B would
        find S0's ``Ships:`` standing again with S0's code gone.
        """
        ship = _change(repo, "s0", "s0", "Ships: plan#4")
        _change(repo, "b", "b", "Reopens: plan#4")
        base = _head(repo)
        _revert(repo, ship)
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]

    def test_undoing_one_of_two_reopens_owes_nothing(self, repo):
        """Review of fe5953d84, HIGH 1(b): the other ``Reopens:`` still cancels the ship.

        S0 ships plan#4, B reopens it, and R0 reverts S0 carrying the
        ``Reopens:`` it owes.  Undoing B leaves R0's ``Reopens:`` over S0, whose
        code is gone, so git's answer stays unshipped and nothing is owed.
        """
        ship = _change(repo, "s0", "s0", "Ships: plan#4")
        reopen = _change(repo, "b", "b", "Reopens: plan#4")
        _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        _revert(repo, reopen)
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_undoing_a_commit_that_ships_and_reopens_a_card_owes_nothing(self, repo):
        """Its own ``Reopens:`` cancels its ``Ships:``: git never called plan#4 shipped."""
        both = _change(repo, "leaf", "leaf", "Ships: plan#4\nReopens: plan#4")
        base = _head(repo)
        _revert(repo, both)
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_undoing_a_ship_and_reopen_of_a_card_shipped_before_owes_ships(self, repo):
        """Its ``Reopens:`` also cancelled the earlier ship, which stands again once undone."""
        _change(repo, "s0", "s0", "Ships: plan#4")
        both = _change(repo, "leaf", "leaf", "Ships: plan#4\nReopens: plan#4")
        base = _head(repo)
        undo = _revert(repo, both)
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert \"leaf\") undoes {both[:12]} (leaf), which carries "
            f"`Reopens: plan#4`; add the trailer `Ships: plan#4` to the last paragraph of "
            f"{undo[:12]}'s message"]

    @pytest.mark.parametrize("undone", ["first", "second"])
    def test_undoing_either_of_two_ships_owes_reopens(self, repo, undone):
        """Decided by the session under ruling R-BAL207 (review of the landing rule, HIGH 1).

        Two commits ship plan#4, which may be its parts (``recurrence:R16-c-2``
        shipped in three).  Undoing either removes code the card shipped with,
        so it owes ``Reopens:``, whichever of the two is undone; once paid, git
        calls plan#4 open until a commit ships it again.
        """
        ships = {name: _change(repo, name, name, "Ships: plan#4") for name in ("first", "second")}
        base = _head(repo)
        undo = _revert(repo, ships[undone])
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert \"{undone}\") undoes {ships[undone][:12]} ({undone}), which "
            f"carries `Ships: plan#4`; add the trailer `Reopens: plan#4` to the last paragraph "
            f"of {undo[:12]}'s message"]
        _run(repo, "commit", "--quiet", "--amend", "--no-edit", "--trailer", "Reopens: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])
        assert not shipped(repo, history(repo, _head(repo)))[0]

    def test_undoing_the_last_standing_ship_on_a_branchy_history_owes_reopens(self, repo):
        """Review of 46d9b578a, finding 1: the case the net-effect rule passed.

        Leaf 1 ships plan#4 and lands; its revert (Reopens) is built on a branch
        that never saw leaf 2, which ships plan#4 again on another; both merge,
        so leaf 2's ship is the one standing.  Reverting leaf 2 with no trailer
        would leave git calling plan#4 shipped with both leaves' code gone.
        """
        leaf1 = _change(repo, "leaf1", "leaf 1", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "-b", "undo")
        _revert(repo, leaf1, trailer="Reopens: plan#4")
        _run(repo, "checkout", "--quiet", "-b", "leaf2", leaf1)
        leaf2 = _change(repo, "leaf2", "leaf 2", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        _run(repo, "merge", "--quiet", "--no-ff", "undo", "-m", "Merge undo")
        _run(repo, "merge", "--quiet", "--no-ff", "leaf2", "-m", "Merge leaf 2")
        base = _head(repo)
        _revert(repo, leaf2)
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]

    def test_a_shortened_reference_id_is_resolved(self, repo):
        """``git revert --reference`` writes ``<short id> (<subject>, <date>)``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        _revert(repo, ship, "--reference")
        assert "This reverts commit " + ship[:7] in _run(repo, "log", "-1", "--format=%B")
        reasons = problems(repo, base, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]

    @pytest.mark.parametrize("order", [("v2", "reopen"), ("reopen", "v2")])
    def test_a_squash_of_two_reverts_is_one_undo(self, repo, order):
        """Review of fe5953d84, MEDIUM 1: its records are judged together, not one by one.

        v1 ships plan#4, a revert reopens it, and v2 ships it again.  One
        commit undoing both v2 and that revert brings v1 back: git calls plan#4
        shipped before and after, so nothing is owed.  Alone, v2's record owes
        ``Reopens:``, so judging only the first record, or only the last, fails
        one of the two orders (review of the landing rule, MEDIUM 3).
        """
        v1 = _change(repo, "v1", "v1", "Ships: plan#4")
        commits = {"reopen": _revert(repo, v1, trailer="Reopens: plan#4"),
                   "v2": _change(repo, "v2", "v2", "Ships: plan#4")}
        base = _head(repo)
        for name in order:
            _revert(repo, commits[name])
        _squash(repo, base)
        assert [named for named, _ in reverts(_run(repo, "log", "-1", "--format=%B"))] == [
            commits[name] for name in order]
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_one_undo_that_removes_a_ship_and_brings_one_back_owes_ships(self, repo):
        """Delta review of the landing rule, LOW 2: a card both removed and brought back.

        A line ships plan#4 (e) and a manual ``Reopens:`` finds it broken;
        ``dev`` ships it (w) and a revert reopens w.  One commit undoing e and
        that revert removes e's code and brings w's back while git calls
        plan#4 open, so it owes ``Ships:``, not nothing.
        """
        _run(repo, "checkout", "--quiet", "-b", "line")
        e = _change(repo, "e", "e", "Ships: plan#4")
        _change(repo, "m", "m", "Reopens: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        w = _change(repo, "w", "w", "Ships: plan#4")
        reopen = _revert(repo, w, trailer="Reopens: plan#4")
        _run(repo, "merge", "--quiet", "--no-ff", "line", "-m", "Merge line")
        base = _head(repo)
        _revert(repo, e)
        _revert(repo, reopen)
        undo = _squash(repo, base)
        assert problems(repo, base, undo)[1] == [
            f"{undo[:12]} (Revert \"e\") undoes {reopen[:12]} (Revert \"w\"), which carries "
            f"`Reopens: plan#4`; add the trailer `Ships: plan#4` to the last paragraph of "
            f"{undo[:12]}'s message"]
        _run(repo, "commit", "--quiet", "--amend", "--no-edit", "--trailer", "Ships: plan#4")
        assert problems(repo, base, _head(repo)) == (1, [])

    def test_a_ships_debt_cites_the_reopens_that_cancelled_the_ship(self, repo):
        """Review of the landing rule, LOW 2: not a stray undone beside it, which moved nothing."""
        stray = _change(repo, "stray", "stray", "Reopens: plan#4")
        ship = _change(repo, "s", "s", "Ships: plan#4")
        reopen = _revert(repo, ship, trailer="Reopens: plan#4")
        base = _head(repo)
        undo = _change(repo, "undo", "Revert two", f"This reverts commit {stray}.",
                       f"This reverts commit {reopen}.")
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert two) undoes {reopen[:12]} (Revert \"s\"), which carries "
            f"`Reopens: plan#4`; add the trailer `Ships: plan#4` to the last paragraph of "
            f"{undo[:12]}'s message"]


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

    def test_a_merge_that_shipped_reopened_and_reshipped_owes_one_reopens(self, repo):
        """Review of 1e52a05ae, S3: the undone commits are taken out together.

        The branch ships plan#4, reverts it (Reopens) and reapplies it (Ships);
        merged, git calls plan#4 shipped, and without the branch's commits it
        does not.  Undoing the merge owes ``Reopens: plan#4`` and nothing else:
        asking each trailer's opposite demanded ``Ships: plan#4`` too, on the
        commit that removes the code.
        """
        _run(repo, "checkout", "--quiet", "-b", "feat")
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        undo = _revert(repo, ship, trailer="Reopens: plan#4")
        _revert(repo, undo, trailer="Ships: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        _run(repo, "merge", "--quiet", "--no-ff", "feat", "-m", "Merge feat")
        merged = _head(repo)
        _revert(repo, merged, "-m", "1")
        reasons = problems(repo, merged, _head(repo))[1]
        assert len(reasons) == 1 and "add the trailer `Reopens: plan#4`" in reasons[0]
        _run(repo, "commit", "--quiet", "--amend", "--no-edit", "--trailer", "Reopens: plan#4")
        assert problems(repo, merged, _head(repo)) == (1, [])

    def test_a_merge_that_shipped_and_reopened_a_card_owes_nothing(self, repo):
        """The branch ships plan#4 and reverts it (Reopens): git never called plan#4 shipped
        on ``dev``, so undoing the merge moves nothing."""
        _run(repo, "checkout", "--quiet", "-b", "feat")
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        _revert(repo, ship, trailer="Reopens: plan#4")
        _change(repo, "other", "other")
        _run(repo, "checkout", "--quiet", "dev")
        _run(repo, "merge", "--quiet", "--no-ff", "feat", "-m", "Merge feat")
        merged = _head(repo)
        _revert(repo, merged, "-m", "1")
        assert problems(repo, merged, _head(repo)) == (1, [])

    def test_undoing_a_merge_that_reverted_and_reshipped_a_card_owes_nothing(self, repo):
        """Review of fe5953d84, MEDIUM 2: undoing the merge brings the first ship back.

        ``dev`` ships plan#4 (v1); a branch reverts v1 (Reopens) and ships v2.
        Undoing the merge removes v2 and restores v1: git calls plan#4 shipped
        before and after, so nothing is owed.
        """
        v1 = _change(repo, "v1", "v1", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "-b", "feat")
        _revert(repo, v1, trailer="Reopens: plan#4")
        _change(repo, "v2", "v2", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        _run(repo, "merge", "--quiet", "--no-ff", "feat", "-m", "Merge feat")
        merged = _head(repo)
        _revert(repo, merged, "-m", "1")
        assert problems(repo, merged, _head(repo)) == (1, [])

    def test_a_merge_of_two_cards_owes_each_card_its_own_trailer(self, repo):
        """Review of fe5953d84, MEDIUM 4: the branch ships plan#4 and reopens plan#5.

        ``dev`` shipped plan#5; the branch ships plan#4 and reverts plan#5's
        commit.  Undoing the merge removes plan#4's code and brings plan#5's
        back, so it owes ``Reopens: plan#4`` AND ``Ships: plan#5``.
        """
        five = _change(repo, "five", "five", "Ships: plan#5")
        _run(repo, "checkout", "--quiet", "-b", "feat")
        four = _change(repo, "four", "four", "Ships: plan#4")
        unfive = _revert(repo, five, trailer="Reopens: plan#5")
        _run(repo, "checkout", "--quiet", "dev")
        _run(repo, "merge", "--quiet", "--no-ff", "feat", "-m", "Merge feat")
        merged = _head(repo)
        undo = _revert(repo, merged, "-m", "1")
        assert problems(repo, merged, _head(repo))[1] == [
            f"{undo[:12]} (Revert \"Merge feat\") undoes {four[:12]} (four), which carries "
            f"`Ships: plan#4`; add the trailer `Reopens: plan#4` to the last paragraph of "
            f"{undo[:12]}'s message",
            f"{undo[:12]} (Revert \"Merge feat\") undoes {unfive[:12]} (Revert \"five\"), "
            f"which carries `Reopens: plan#5`; add the trailer `Ships: plan#5` to the last "
            f"paragraph of {undo[:12]}'s message"]
        _run(repo, "commit", "--quiet", "--amend", "--no-edit",
             "--trailer", "Reopens: plan#4", "--trailer", "Ships: plan#5")
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
            f"repository does not hold (if that commit was rebased, cite its new id)"]

    def test_a_revert_of_a_commit_outside_its_own_history_is_refused(self, repo):
        """Review of the landing rule, HIGH 2: ``dev`` holds a cherry-picked copy, and the
        record names the original, so taking its trailers out of ``dev``'s history would take
        out nothing."""
        _run(repo, "checkout", "--quiet", "-b", "side")
        original = _change(repo, "leaf", "leaf", "Ships: plan#4")
        _run(repo, "checkout", "--quiet", "dev")
        _change(repo, "other", "other")
        _run(repo, "cherry-pick", original)
        assert _head(repo) != original, "control: the copy is another commit"
        base = _head(repo)
        undo = _revert(repo, original)
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert \"leaf\"): it says it reverts commit {original[:12]}, which "
            f"is not in the history it lands on (if that commit was rebased or cherry-picked, "
            f"cite the copy's id)"]

    def test_a_commit_with_an_unreadable_record_is_asked_no_debt(self, repo):
        """What it undoes is not all known, so neither is what it owes: only the record is
        refused, and the readable record's ship asks no ``Reopens:``."""
        ship = _change(repo, "leaf", "leaf", "Ships: plan#4")
        base = _head(repo)
        undo = _change(repo, "gone", "Revert",
                       f"This reverts commit {ship}.\n\nThis reverts commit {'e' * 40}.")
        assert problems(repo, base, _head(repo))[1] == [
            f"{undo[:12]} (Revert): it says it reverts commit {'e' * 40}, which this "
            f"repository does not hold (if that commit was rebased, cite its new id)"]

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

    def test_an_empty_change_set_exits_two_not_green(self, repo, capsys):
        """No event has an empty change set: one means the ends are wrong, nothing checked."""
        assert main([_head(repo), _head(repo)], root=repo) == 2
        assert "holds no commit, so nothing was checked" in capsys.readouterr().err

    def test_a_range_git_cannot_read_exits_two(self, repo, capsys):
        """An id this repository does not hold is a failure, never an empty change set."""
        assert main(["e" * 40, _head(repo)], root=repo) == 2
        assert "git could not read" in capsys.readouterr().err
