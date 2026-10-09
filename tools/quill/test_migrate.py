"""The ``migrate`` command's ``check``: its report and exit status, on the small corpus.

``main`` takes the registries' reader and the repository root, so the command runs on
:mod:`_migrate_fake`'s plan and the ``code`` fixture's throwaway repository; nothing reads
the live registries, and nothing writes anywhere.
"""
from __future__ import annotations

import json

import pytest

from tools.ci.scratch import run
from tools.quill import _migrate_source, migrate, quill
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.quill._migrate_fake import complete_input
from tools.quill._migrate_source import read_source


def _run(corpus, code, path, capsys):
    """``migrate check --input path`` on the corpus; its exit status and output."""
    status = migrate.main(["check", "--input", str(path)],
                          read=lambda commit: corpus, root=code)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_a_clean_check_prints_what_moves_and_exits_0(corpus, code, tmp_path, capsys):
    """Every count by arc and kind, the census, the bare #N count, and the write estimate:
    the two rulings' 4, then the steps' 18, the findings' 9, the questions' 12 and the one
    milestone's create (:func:`migrate.writes`)."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, _ = _run(corpus, code, path, capsys)
    lines = out.splitlines()
    assert status == 0
    assert lines[0] == f"check at {corpus.commit}: 13 cards, 13 to file"
    assert lines[1] == "  balance: steps 4, findings 2, questions 2, rulings 1"
    assert "  salary: steps 1, findings 1, questions 1, rulings 1" in lines
    assert ("census balance: 16 lines -- on a card 11; a heading 2; a note, moving nowhere 2; "
            "a shipped step's pointer, moving nowhere 1") in lines
    assert "cards holding a bare #N, which GitHub links to card N: none" in lines
    assert lines[-2:] == [
        "writes if every card is new: 4 filing the rulings, then 40 filing the rest and the 1 "
        "milestones, before the board's moves",
        "0 refusal(s)"]


def test_the_write_estimate_counts_each_write_of_a_new_card(corpus):
    """A ruling: create, then its close with its mark (one PATCH).  A-2b: create, parent
    link, one blocker link, board place, unmark.  A question: create, comment, board
    place, unmark."""
    items = {item.key: item for item in read_source(corpus).items}
    assert [migrate.writes(items[key]) for key in (
        "balance:R-A", "balance:A-2b", "balance:Q-2", "balance:A-2", "balance:F-1")] == [
        2, 5, 4, 2, 3]


def test_a_refusal_is_printed_and_exits_1(corpus, code, tmp_path, capsys):
    """One missing name: one REFUSED line, exit 1."""
    data = complete_input(corpus)
    del data["names"]["balance:A-3"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    status, out, _ = _run(corpus, code, path, capsys)
    assert status == 1
    assert [line for line in out.splitlines() if line.startswith("REFUSED")] == [
        "REFUSED: names: balance:A-3: no name"]
    assert out.splitlines()[-1] == "1 refusal(s)"


def test_an_unreadable_input_or_git_failure_exits_2_before_any_check(corpus, code, tmp_path,
                                                                     capsys):
    """No file, a file that is not a JSON object, a root that is no repository: each fails
    with exit 2 and checks nothing."""
    listed = tmp_path / "list.json"
    listed.write_text("[]")
    assert _run(corpus, code, tmp_path / "missing.json", capsys)[0] == 2
    status, out, err = _run(corpus, code, listed, capsys)
    assert (status, out) == (2, "") and "holds list, not an object" in err
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    status, out, err = _run(corpus, nowhere, path, capsys)
    assert (status, out) == (2, "") and err.startswith("failed: git rev-parse HEAD")


def test_planning_files_that_differ_from_head_are_refused_before_any_check(
        corpus, code, tmp_path, capsys):
    """Every card's As filed block names HEAD's commit, so a checkout whose planning files
    differ from HEAD would cite a commit that does not hold their text (M1 of B1's
    review): exit 2, nothing checked."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    (code / "docs" / "plans").mkdir(parents=True)
    (code / "docs" / "plans" / "steps.md").write_text("| an uncommitted edit |\n")
    status, out, err = _run(corpus, code, path, capsys)
    assert (status, out) == (2, "")
    assert err.startswith("failed: the planning files differ from HEAD") and "steps.md" in err


def test_an_input_file_inside_the_repository_is_refused(corpus, code, capsys):
    """Its text may quote a real figure, which the public repository may not hold
    (R-BAL132; M6 of B1's review): exit 2, before it is even read."""
    path = code / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, err = _run(corpus, code, path, capsys)
    assert (status, out) == (2, "") and "is inside the repository" in err


def test_a_card_filed_already_is_counted_apart_and_takes_only_its_links(
        corpus, code, tmp_path, capsys, monkeypatch):
    """S-1 mapped (as X-cx is card #1): 13 cards, 12 to file, and its writes are its links
    alone (none here), so the rest's estimate drops by its 3."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, _ = _run(corpus, code, path, capsys)
    lines = out.splitlines()
    assert status == 0
    assert lines[0] == (f"check at {corpus.commit}: 13 cards, 12 to file (salary:S-1 is "
                        "plan#1 already)")
    assert lines[-2].startswith("writes if every card is new: 4 filing the rulings, then 37 ")


@pytest.mark.parametrize("change", ["modified", "staged", "readme"])
def test_a_tracked_planning_file_changed_since_head_is_refused(corpus, code, tmp_path, capsys,
                                                               change):
    """M1's own case, a TRACKED planning file edited since HEAD -- unstaged, staged, and the
    one arc document outside docs/plans (review of B1's fixes, MEDIUM-2)."""
    readme = code / "docs" / "audits" / "balance_architecture" / "README.md"
    steps = code / "docs" / "plans" / "steps.md"
    for path in (readme, steps):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("committed\n")
    run(code, "add", "-A")
    run(code, "commit", "-q", "-m", "planning files")
    edited = readme if change == "readme" else steps
    edited.write_text("edited\n")
    if change == "staged":
        run(code, "add", str(edited))
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, err = _run(corpus, code, path, capsys)
    assert (status, out) == (2, "") and edited.name in err


def test_planning_files_that_are_not_utf8_exit_2(corpus, code, tmp_path, capsys):
    """A planning file that is not UTF-8 fails like any unreadable one (review of B1, L3)."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))

    def unreadable(_commit):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    status = migrate.main(["check", "--input", str(path)], read=unreadable, root=code)
    assert (status, capsys.readouterr().out) == (2, "")


def test_a_card_filed_already_writes_its_links(corpus, monkeypatch):
    """A-2b mapped: its parent link and its one blocker link, nothing else (review of B1's
    fixes, L2a)."""
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-2b", 1)
    item = next(item for item in read_source(corpus).items if item.key == "balance:A-2b")
    assert migrate.writes(item) == 2


def test_quill_file_step_takes_both_release_labels():
    """``--label`` offers the two labels a release reads, from their one home."""
    args = quill.parser().parse_args(["file", "step", "--arc", "balance", "--title", "t",
                                      "--body-file", "b", "--label", "moves-money",
                                      "--label", "deploy-together"])
    assert args.label == [MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL]
