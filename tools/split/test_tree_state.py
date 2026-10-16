"""Where a record is judged (the coordinator's ruling on delta review 2, REVISED (B)): record
and apply refuse a working tree that is not ``HEAD``'s, judged by its files whatever the
index or a machine's own rules say (delta review 3's H-1), and record a source ``HEAD`` does
not track; apply and prove refuse another ``HEAD`` or environment than the record's; prove
refuses a tree that differs from ``HEAD`` anywhere but at exactly the paths apply writes;
and record and prove refuse a change made while their runs ran (delta review 3's M-1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split.conftest import IMPORTER, PACKAGE, SOURCE, applied, committed, everything
from tools.split.split import _META

#: The tracked file most of the cases below change.
TRACKED = "tests/shared.py"

#: A conftest pytest loads for the source's tests, which the sample does not hold.
AREA_CONFTEST = "tests/test_area/conftest.py"


def _edited(root: Path) -> None:
    """A tracked file edited."""
    path = root / TRACKED
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")


def _importer_added(root: Path) -> None:
    """An untracked module importing the source."""
    (root / "tests/test_late.py").write_text("from tests.test_area.test_sample import _make\n",
                                             encoding="utf-8")


def _flagged_edit(flag: str, path: str):
    """``path`` flagged in the index with ``flag``, then edited: a change ``git status``
    never compares (delta review 3's P3, and its P7 for the source)."""
    def change(root: Path) -> None:
        scratch.run(root, "update-index", flag, path)
        target = root / path
        target.write_text(target.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    return change


def _excluded_conftest(root: Path) -> None:
    """An untracked conftest pytest would load, named in ``.git/info/exclude``, a rule kept
    per machine (delta review 3's P3b)."""
    (root / AREA_CONFTEST).write_text('"""Area."""\n', encoding="utf-8")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text(f"{AREA_CONFTEST}\n", encoding="utf-8")


def _hiding_gitignore(root: Path) -> None:
    """An untracked ``.gitignore`` holding ``*`` beside an untracked conftest, hiding both
    (the thirteenth unit correction)."""
    (root / AREA_CONFTEST).write_text('"""Area."""\n', encoding="utf-8")
    (root / AREA_CONFTEST).with_name(".gitignore").write_text("*\n", encoding="utf-8")


def _staged(root: Path) -> None:
    """A new file added to the index and not committed."""
    (root / "notes.txt").write_text("staged\n", encoding="utf-8")
    scratch.run(root, "add", "notes.txt")


#: Each way the working tree can differ from HEAD: the one path it differs at, and the change.
DIFFERENCES = {
    "a tracked file edited": (TRACKED, _edited),
    "a tracked file deleted": (TRACKED, lambda root: (root / TRACKED).unlink()),
    "a tracked file's mode changed": (TRACKED, lambda root: (root / TRACKED).chmod(0o755)),
    "an untracked module importing the source": ("tests/test_late.py", _importer_added),
    "a new file staged": ("notes.txt", _staged),
    "a tracked file edited under --skip-worktree": (
        TRACKED, _flagged_edit("--skip-worktree", TRACKED)),
    "a tracked file edited under --assume-unchanged": (
        TRACKED, _flagged_edit("--assume-unchanged", TRACKED)),
    "the source edited under --skip-worktree": (SOURCE, _flagged_edit("--skip-worktree",
                                                                      SOURCE)),
    "an untracked conftest named in .git/info/exclude": (AREA_CONFTEST, _excluded_conftest),
    "an untracked .gitignore hiding an untracked conftest": ("tests/test_area/.gitignore",
                                                             _hiding_gitignore),
}


#: What each command says it did not do.
DONE = {"record": "recorded", "apply": "applied"}


@pytest.mark.parametrize("difference", DIFFERENCES)
@pytest.mark.parametrize("command", ["record", "apply"])
def test_a_tree_that_is_not_heads_is_refused(split, capsys, command, difference):
    """record and apply each refuse a working tree that differs from HEAD, naming where,
    before anything is written or discarded: the earlier record, the tree and the id maps are
    as they were (the coordinator's ruling on delta review 2, REVISED (B) (1)).  A file the
    index flags, one a machine's own rule hides and an untracked ``.gitignore`` are each a
    difference (delta review 3's H-1 and the thirteenth unit correction): the source edited
    under ``--skip-worktree`` after record was refused only by apply's source hash check,
    which this made unnecessary and deleted."""
    assert split("record") == 0
    path, change = DIFFERENCES[difference]
    change(split.root)
    before = everything(split)
    capsys.readouterr()
    assert split(command) == 1
    err = capsys.readouterr().err
    assert f"REFUSED: the working tree differs from HEAD at 1 path(s), e.g. ['{path}']" in err
    assert err.endswith(f"nothing is {DONE[command]}\n")
    assert everything(split) == before


def _moved(split, _monkeypatch) -> None:
    """A commit after the record: HEAD moves, and the tree is HEAD's."""
    (split.root / "README.md").write_text("A commit after the record.\n", encoding="utf-8")
    committed(split.root, "after the record")


def _another_environment(_split, monkeypatch) -> None:
    """The process runs with another interpreter, distributions or git than record's."""
    monkeypatch.setattr(run, "environment", lambda _root: "0" * 64)


def _no_environment(split, _monkeypatch) -> None:
    """A record an earlier version of the tool wrote, holding no environment."""
    path = split.into / _META
    meta = json.loads(path.read_text(encoding="utf-8"))
    del meta["environment"]
    path.write_text(json.dumps(meta), encoding="utf-8")


#: Each state a record is not judged in, as a change made after it.
ELSEWHERE = {"HEAD moved": _moved, "another environment": _another_environment,
             "no environment kept": _no_environment}

#: What each command's refusal ends with.
TAILS = {"apply": "record again; nothing is applied",
         "prove": "a split is proved where it was recorded, before it is committed"}


@pytest.mark.parametrize("state", ELSEWHERE)
@pytest.mark.parametrize("command", ["apply", "prove"])
def test_a_record_is_judged_only_where_it_was_taken(split, capsys, monkeypatch, command,
                                                    state):
    """apply and prove each refuse unless HEAD and the environment are the record's; a record
    holding no environment is another version's (REVISED (B) (1) and (2)).  For prove, HEAD
    moved by committing the split itself, the tree then HEAD's."""
    assert split("record") == 0
    if command == "prove":
        assert split("apply") == 0
    taken = scratch.run(split.root, "rev-parse", "HEAD")
    ELSEWHERE[state](split, monkeypatch)
    before = everything(split)
    capsys.readouterr()
    assert split(command) == 1
    said = (f"HEAD is {scratch.run(split.root, 'rev-parse', 'HEAD')[:9]}, and the record was "
            f"taken at {taken[:9]}") if state == "HEAD moved" else (
        "the record was taken with another interpreter, other installed distributions or "
        "another git, or by a version of this tool that did not record them")
    assert f"REFUSED: {said}; {TAILS[command]}" in capsys.readouterr().err
    assert everything(split) == before


def _package_only(split) -> None:
    """An apply killed once the package was written: the source and importer as before."""
    scratch.run(split.root, "checkout", "--quiet", "--", SOURCE, IMPORTER)


def _part_of_the_package(split) -> None:
    """An apply killed while the package was written."""
    _package_only(split)
    for name in ("test_first.py", "test_big_late.py"):
        (split.root / PACKAGE / name).unlink()


#: Each tree prove refuses after an apply, and the two lists its refusal names.
NOT_THE_WRITES = {
    "killed once the package was written": (_package_only, [SOURCE, IMPORTER], []),
    "killed while the package was written": (_part_of_the_package, [
        SOURCE, f"{PACKAGE}/test_big_late.py", f"{PACKAGE}/test_first.py", IMPORTER], []),
    "a change beside the split": (
        lambda split: (split.root / "notes.txt").write_text("x\n", encoding="utf-8"),
        [], ["notes.txt"]),
}


@pytest.mark.parametrize("shape", NOT_THE_WRITES)
def test_prove_refuses_a_tree_other_than_what_apply_wrote(split, capsys, shape):
    """prove refuses unless the tree differs from HEAD at EXACTLY the paths apply writes: a
    partial write set (an apply ``SIGKILL`` ended) is never proved, and a change beside the
    split is judged by no arm (the coordinator's ruling on 3B-2's signal fork, (d))."""
    applied(split)
    change, missing, beside = NOT_THE_WRITES[shape]
    change(split)
    capsys.readouterr()
    assert split("prove") == 1
    assert ("REFUSED: the working tree must differ from HEAD at exactly the paths apply "
            f"writes: {len(missing)} written by apply and not different, e.g. {missing}; "
            f"{len(beside)} different and not written by apply, e.g. {beside}"
            in capsys.readouterr().err)


def _committed_beside(split, _monkeypatch) -> None:
    """A file beside the split committed: ``HEAD`` moves, and the tree's differences stay."""
    (split.root / "README.md").write_text("A commit while the runs ran.\n", encoding="utf-8")
    scratch.run(split.root, "add", "README.md")
    scratch.run(split.root, "commit", "--quiet", "-m", "while the runs ran")


#: Each change made while a command's runs run, and how its refusal names it.
DURING = {
    "a tracked file edited": (lambda split, _monkeypatch: _edited(split.root),
                              f"the working tree changed at 1 path(s), e.g. ['{TRACKED}']"),
    "a file written beside the split": (
        lambda split, _monkeypatch: (split.root / "notes.txt").write_text(
            "x\n", encoding="utf-8"),
        "the working tree changed at 1 path(s), e.g. ['notes.txt']"),
    "a commit": (_committed_beside, "HEAD moved from "),
    "another environment": (_another_environment, "the environment changed"),
}

#: What each command's refusal of a change while its runs ran ends with.
WITHHELD = {"record": "nothing is recorded", "prove": "no block is printed"}


@pytest.mark.parametrize("change", DURING)
@pytest.mark.parametrize("command", ["record", "prove"])
def test_a_change_while_the_runs_ran_is_refused(split, capsys, monkeypatch, command, change):
    """record and prove judge HEAD, the environment and the tree again after their runs: a
    change made while they ran is refused, naming it, and record writes no token, prove
    prints no block (the coordinator's ruling on delta review 3's M-1, 2026-10-16: each was
    judged only at its start, and a run is minutes long)."""
    if command == "prove":
        applied(split)
    make, said = DURING[change]
    stub, made = run.observe, []

    def observe(*args, **kwargs):
        if not made:
            made.append(make(split, monkeypatch))
        return stub(*args, **kwargs)
    monkeypatch.setattr(run, "observe", observe)
    capsys.readouterr()
    assert split(command) == 1
    out, err = capsys.readouterr()
    assert f"REFUSED: {said}" in err
    assert err.endswith(f" while the runs ran, so no observation was made in the state judged; "
                        f"{WITHHELD[command]}\n")
    assert out == ""
    if command == "record":
        assert not (split.into / _META).exists()


def _untracked(root: Path, ignored: bool) -> None:
    """The source taken out of ``HEAD`` and left on disk untracked, named by the committed
    ``.gitignore`` too when ``ignored``."""
    if ignored:
        (root / ".gitignore").write_text(f"{SOURCE}\n", encoding="utf-8")
        scratch.run(root, "add", ".gitignore")
    scratch.run(root, "rm", "--cached", "--quiet", SOURCE)
    scratch.run(root, "commit", "--quiet", "-m", "the source untracked")


@pytest.mark.parametrize("ignored,said", [
    (False, f"the working tree differs from HEAD at 1 path(s), e.g. ['{SOURCE}']"),
    (True, f"{SOURCE} is not a file HEAD tracks, and a record is judged only against HEAD"),
], ids=["untracked", "untracked and ignored"])
def test_record_refuses_a_source_head_does_not_track(split, capsys, ignored, said):
    """A record is judged only against HEAD, so its source must be a file HEAD tracks: an
    untracked source is a difference, and one the committed rules also ignore is no
    difference, so it is refused by name (the coordinator's ruling on 3B-2b's REVISED (B)
    consequences, item 2, which deletes apply's source and importer re-checks)."""
    _untracked(split.root, ignored)
    capsys.readouterr()
    assert split("record") == 1
    assert f"REFUSED: {said}" in capsys.readouterr().err
    assert not (split.into / _META).exists()
