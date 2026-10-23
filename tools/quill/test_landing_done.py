"""CI's landing check, rule 8 (ruling ``balance:R-BAL385``): a marker a change leaves as it was
may not make its card read shipped; and rule 1 read as every reader of the markers reads them.

Rule 8 compares the cards BEFORE (the merge of the change's first parent with the other
integration branch) and AFTER (the merge of the change with it) say shipped.  Its first case
is X-cx L8's resync probe (``plan-tracker-2026-10-23/m3_resync_probe.md``, measured
2026-10-23): a resync built by hand that keeps the marker ``main``'s hotfix deleted, which
writes nothing on ``dev``'s line and so passed every rule that reads what a change writes.
Every history is built and judged by the builders ``test_landing.py`` shares
(:mod:`tools.quill._histories`); no test here reads the tracker.
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_main, run
from tools.quill import _git
from tools.quill._git import marker
from tools.quill._histories import MON, TUE, claim, judging, landed, merge, never, pull, start
from tools.quill._histories import undone, verdict
from tools.quill.landing import main

#: When a third claim of one card is made.
WED = "2026-10-21T08:00:00Z"


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit; the
    repository and the base (:func:`_histories.start`)."""
    return start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """Judge pull request #600 of branch ``head`` at ``head_sha`` into ``base``, whose tip is
    ``onto`` (:func:`_histories.judging`), the tracker never read: its exit status, output
    and error."""
    judged = judging(capsys, code[0])
    return lambda onto, head_sha, head, base="dev": judged(onto, head_sha, head, base, never)


def _resolved(root, tree_of, first, second, message):
    """A merge of ``first`` and ``second`` whose tree is ``tree_of``'s, as a resolution
    written by hand writes it, whatever git's own merge would hold; its sha."""
    return run(root, "commit-tree", f"{tree_of}^{{tree}}", "-p", first, "-p", second, "-m",
               message)


def _hotfixed(root, base, number):
    """Card ``number`` landed on dev, a release taking it to main, and a hotfix on main
    deleting its marker and its work (the probe's history); dev's tip and main's, main's
    pointed at."""
    dev = landed(root, base, number)
    point_main(root, main_tip := undone(root, merge(root, base, dev, "release"), number))
    return dev, main_tip


def _reshipped(root, base, number):
    """The ruling's pass case: :func:`_hotfixed`, and dev, meanwhile, undid card ``number``
    and shipped it again from a fresh claim, a new line: dev's tip and main's; whose merge
    conflicts on the marker, dev's line changed and main's deleted."""
    dev, main_tip = _hotfixed(root, base, number)
    return landed(root, undone(root, dev, number), number, TUE), main_tip


def _refused(number, other="origin/main", target="origin/dev"):
    """Rule 8's refusal of card ``number``, made shipped by a change into ``target``."""
    return (f"REFUSED -- plan/ships/{number}: card {number} would read shipped once this change "
            f"and {other} meet, as it does not once {target} and {other} do, though this change "
            "writes no line of it: a merge keeping a marker the other side deleted re-ships a "
            "card with no change to its record, so a release would count none of its code; "
            f"take the delete their merge takes, or claim card {number} again (quill claim "
            f"{number}), which writes a new line")


# ------------------------------------------------- a marker left as it was made shipped


def test_a_resync_keeping_the_marker_mains_hotfix_deleted_is_refused(check, code):
    """The probe: card 7 lands on dev and is released; a hotfix on main deletes its marker and
    its code.  A resync built by hand takes main's code but keeps the marker: it writes
    nothing under ``plan/ships/`` on dev's line, yet card 7 would read shipped with no claim,
    and the next release would count it with none of its code.  Refused."""
    root, base = code
    dev, main_tip = _hotfixed(root, base, 7)
    kept = commit_files(root, dev, "keep the marker", {"w7.txt": None})
    status, out, _ = check(dev, _resolved(root, kept, dev, main_tip, "resync"), "resync")
    assert (status, out.splitlines()) == (1, [_refused(7), verdict("resync", 0, 1)])


@pytest.mark.parametrize("head", ["main", "resync"])
def test_gits_own_resync_takes_the_delete_and_lands(check, code, head):
    """The usual resync, main itself or a branch of git's own merge of the two, takes the
    hotfix's delete: a delete, which lands."""
    root, base = code
    dev, main_tip = _hotfixed(root, base, 7)
    resync = main_tip if head == "main" else merge(root, dev, main_tip, "resync")
    assert check(dev, resync, head)[:2] == (0, verdict(head, 1, 0) + "\n")


def test_a_release_keeping_a_marker_dev_deleted_is_refused(check, code):
    """The same rule into main: hotfix 9 lands on main and is resynced to dev, which then
    reverts it.  A release built by hand that keeps the marker would make card 9 read
    shipped again with no change to its record: refused."""
    root, base = code
    hotfix = landed(root, base, 9)
    point_main(root, hotfix)
    dev = undone(root, merge(root, base, hotfix, "resync"), 9)
    run(root, "update-ref", "refs/remotes/origin/dev", dev)
    kept = commit_files(root, dev, "keep the marker", {marker(9): f"claimed {MON}\n"})
    status, out, _ = check(hotfix, _resolved(root, kept, dev, hotfix, "release"), "release",
                           base="main")
    assert (status, out.splitlines()[0]) == (
        1, _refused(9, other="origin/dev", target="origin/main"))


def test_a_later_queue_entry_reads_before_at_the_merge_the_earlier_one_makes(capsys, code):
    """A group of two pull requests into dev, whose tip has not moved yet: card/8's entry,
    then an app change.  The app change's run reads BEFORE at the merge card/8's entry makes
    (M's first parent), where card 8 already reads shipped, never at dev's tip, where it does
    not: it lands."""
    root, base = code
    first = merge(root, base, claim(root, base, 8)[1], "Merge pull request #601")
    app = commit_files(root, base, "an app change", {"app.py": "app\n"})
    later = merge(root, first, app, "Merge pull request #602")
    assert main(["merge_group", later, f"refs/heads/gh-readonly-queue/dev/pr-602-{first}"],
                never, pull("feat/app", app), root) == 0
    assert "0 ship marker change(s), 0 refused" in capsys.readouterr().out


# ------------------------------------------------------- a marker that conflicts BEFORE


def test_a_resync_keeping_the_line_dev_shipped_again_lands(check, code):
    """The ruling's pass case: dev shipped card 7 again from a fresh claim while main's
    hotfix deleted its first line, so the two conflict on its marker.  A resync built by hand
    keeping dev's new line, judged where it landed and never held by main, settles it: it
    lands."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    assert check(dev, _resolved(root, dev, dev, main_tip, "resync"), "resync")[:2] == (
        0, verdict("resync", 0, 0) + "\n")


@pytest.mark.parametrize(("line", "want"), [(MON, 1), (None, 0)],
                         ids=["the line dev undid", "main's delete"])
def test_a_resync_writing_a_conflicted_marker_is_judged_by_what_it_writes(check, code, line,
                                                                         want):
    """The same conflict, settled by writing the marker: back to the first line, which dev
    held once and undid (rule 3: refused), or main's delete (rule 2: it lands)."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    written = commit_files(root, dev, "settle", {marker(7): line and f"claimed {line}\n"})
    status, out, _ = check(dev, _resolved(root, written, dev, main_tip, "resync"), "resync")
    assert status == want
    assert ("origin/dev held this very line once and undid it" in out) is (line is not None)


def test_a_resync_settling_a_conflict_on_a_line_main_undid_is_refused(check, code):
    """Two claims of card 7 (lines Y and Z) cross: dev merges Z into Y keeping Y, and main
    merges Y into Z also keeping Y, then main's hotfix deletes Y.  The two then have two
    merge bases whose own merge conflicts, so dev's and main's merge conflicts on the marker
    though main undid dev's very line.  A resync built by hand keeping Y would re-ship what
    main undid: refused, though the marker conflicted BEFORE."""
    root, base = code
    one = commit_files(root, base, "claim Y", {marker(7): f"claimed {MON}\n"})
    two = commit_files(root, base, "claim Z", {marker(7): f"claimed {TUE}\n"})
    dev = _resolved(root, one, one, two, "dev keeps Y")
    took = _resolved(root, one, two, one, "main takes Y")
    point_main(root, main_tip := commit_files(root, took, "hotfix", {marker(7): None}))
    status, out, _ = check(dev, _resolved(root, dev, dev, main_tip, "resync"), "resync")
    assert (status, out.splitlines()[0]) == (1, (
        "REFUSED -- plan/ships/7: origin/dev and origin/main conflict on it, and this change "
        "settles it on a line origin/main held once and undid, though it writes no line of "
        "it, so it would re-ship card 7 with no change to its record, and a release would "
        "count none of its code: a card undone ships again from a fresh claim (quill claim "
        "7), which writes a new line"))


@pytest.mark.parametrize("again", ["a fresh claim", "a revert of its revert"])
def test_a_branch_bringing_mains_delete_lands_where_main_shipped_the_card_again(check, code,
                                                                                again):
    """Card 7 lands on dev and is released; main's hotfix deletes it and dev reverts it too.
    main then ships it again with a new line -- from a fresh claim, or, in a history main's
    own rule 3 refuses but git can hold, a fresh claim reverted and its revert reverted --
    so dev and main conflict on its marker.  A branch that merged main's hotfix in settles
    the conflict on main's line, which main holds: card 7 shipped there, so it lands."""
    root, base = code
    dev = landed(root, base, 7)
    hotfix = undone(root, merge(root, base, dev, "release"), 7)
    shipped = landed(root, hotfix, 7, TUE)
    if again == "a revert of its revert":
        shipped = commit_files(root, undone(root, shipped, 7), 'Revert "Revert plan#7"',
                               {marker(7): f"claimed {TUE}\n", "w7.txt": "work\n"})
    point_main(root, shipped)
    reverted = undone(root, dev, 7)
    feature = commit_files(root, merge(root, reverted, hotfix, "merge main in"), "work",
                           {"x.py": "x\n"})
    assert check(reverted, feature, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


@pytest.mark.parametrize("into", ["dev", "main"])
def test_an_app_change_lands_while_dev_and_main_conflict_on_a_marker(check, code, into):
    """In a week dev and main conflict on card 7's marker, a change of app code alone, into
    either, leaves the conflict as it was: it lands, the tracker never read."""
    root, base = code
    dev, main_tip = _reshipped(root, base, 7)
    run(root, "update-ref", "refs/remotes/origin/dev", dev)
    onto = dev if into == "dev" else main_tip
    head = commit_files(root, onto, "an app change", {"app.py": "app\n"})
    assert check(onto, head, "feat/app", base=into)[:2] == (
        0, verdict("feat/app", 0, 0, into) + "\n")


def test_an_app_change_lands_while_a_file_meets_a_directory_at_a_marker(check, code):
    """L1's review, its L1: dev changes card 7's line while main makes ``plan/ships/7`` a
    directory (a stray rule 1 refuses wherever it is written, so built here by hand).  git
    names that conflict after each merge's own tips, so BEFORE's name and AFTER's differ; read
    by its path, it is one conflict before and after an app change, which lands."""
    root, base = code
    both = commit_files(root, base, "seven", {marker(7): f"claimed {MON}\n"})
    dev = commit_files(root, both, "dev", {marker(7): f"claimed {TUE}\n"})
    point_main(root, commit_files(root, commit_files(root, both, "main", {marker(7): None}),
                                  "a directory", {f"{marker(7)}/x": "x\n"}))
    head = commit_files(root, dev, "an app change", {"app.py": "app\n"})
    assert check(dev, head, "feat/app")[:2] == (0, verdict("feat/app", 0, 0) + "\n")


def test_a_delete_of_a_marker_main_changed_lands_though_the_two_then_conflict(check, code):
    """L1's review, its L3: rule 8 reads only a marker a change leaves, so a marker it writes
    stays rules 1-7's.  Card 7 lands on dev and is released; main's hotfix deletes it and main
    ships it again from a fresh claim.  A revert of card 7 on dev deletes dev's line, which
    main changed: a delete (rule 2), which lands, though dev and main then conflict on it."""
    root, base = code
    dev, hotfix = _hotfixed(root, base, 7)
    point_main(root, landed(root, hotfix, 7, TUE))
    reverted = undone(root, dev, 7)
    assert check(dev, reverted, "revert-7")[:2] == (0, verdict("revert-7", 1, 0) + "\n")
    assert _git.merge(root, (reverted, _git.TIPS[1])).conflicts == (marker(7),)


def test_a_conflict_a_change_brings_without_writing_it_refuses_to_judge(check, code):
    """Hotfix 8 lands on main (line W), is undone there, and ships again (line Z).  A branch
    that merged main's first landing in, dropping its marker by hand, would make dev and main
    conflict on it once it lands, where before they merge cleanly: whether card 8 shipped
    would be unknown, so nothing is judged (exit 2)."""
    root, base = code
    first = landed(root, base, 8)
    point_main(root, landed(root, undone(root, first, 8), 8, WED))
    dropped = commit_files(root, merge(root, base, first, "merge main in"), "drop it",
                           {marker(8): None})
    status, out, err = check(base, dropped, "feat/x")
    assert (status, out) == (2, "")
    assert ("merge with a conflict on plan/ships/8, which origin/dev and origin/main merge "
            "without, though this change writes none of them") in err


# ------------------------------------------- rule 1: a marker is read as M's tree reads it


def _gitlink(root, on, number):
    """A commit on ``on`` adding, at card ``number``'s marker, a submodule's commit (a
    gitlink, mode 160000) instead of a file; its sha."""
    run(root, "checkout", "--quiet", "--detach", on)
    run(root, "update-index", "--add", "--cacheinfo", f"160000,{on},{marker(number)}")
    run(root, "commit", "--quiet", "-m", f"a gitlink at plan/ships/{number}")
    return run(root, "rev-parse", "HEAD")


def _no_file(number):
    """Rule 1's refusal of a gitlink at card ``number``'s marker."""
    return (f"REFUSED -- plan/ships/{number}: a marker is a file, and what this change writes "
            f"there is none (a submodule's commit), which no reader counts as card {number}'s "
            "marker, so no change may add or change it")


def test_a_gitlink_at_a_cards_marker_is_no_marker_even_from_its_claim(check, code):
    """card/7 adds a gitlink at ``plan/ships/7``: every reader of the markers names it no
    card's (``_git.markers_in``), so rule 1 refuses it, and card/7 records nothing."""
    root, base = code
    status, out, _ = check(base, _gitlink(root, base, 7), "card/7")
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- card/7 adds or changes no plan/ships/7, so it records nothing, though a "
        "change from card/7 records card 7 (pushed again after its merge? later work is a "
        "card of its own)", _no_file(7), verdict("card/7", 1, 2)])


@pytest.mark.parametrize("beside", [False, True], ids=["alone", "beside a marker file"])
def test_a_gitlink_through_the_records_door_is_no_marker(check, code, beside):
    """A change into main adding a gitlink at ``plan/ships/2``, alone or beside card 3's
    marker file, adds something that is no card's marker, so it is no records-only change:
    rule 1 refuses the gitlink, card 3 may then be recorded only by card/3, and the tracker
    is never read."""
    root, base = code
    on = commit_files(root, base, "records", {marker(3): "landed before\n"}) if beside else base
    status, out, _ = check(base, _gitlink(root, on, 2), "records", base="main")
    assert (status, out.splitlines()) == (1, [_no_file(2), *(
        ["REFUSED -- plan/ships/3: records adds it, but only card 3's own claim, card/3, may "
         "record it"] if beside else []), verdict("records", 1 + beside, 1 + beside, "main")])
