"""Where a record is judged (the coordinator's ruling on delta review 2, REVISED (B)): record
and apply refuse a working tree that is not ``HEAD``'s, apply and prove refuse another
``HEAD`` or environment than the record's, and prove refuses a tree that differs from
``HEAD`` anywhere but at exactly the paths apply writes."""
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


def _edited(root: Path) -> None:
    """A tracked file edited."""
    path = root / TRACKED
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")


def _importer_added(root: Path) -> None:
    """An untracked module importing the source."""
    (root / "tests/test_late.py").write_text("from tests.test_area.test_sample import _make\n",
                                             encoding="utf-8")


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
}


#: What each command says it did not do.
DONE = {"record": "recorded", "apply": "applied"}


@pytest.mark.parametrize("difference", DIFFERENCES)
@pytest.mark.parametrize("command", ["record", "apply"])
def test_a_tree_that_is_not_heads_is_refused(split, capsys, command, difference):
    """record and apply each refuse a working tree that differs from HEAD, naming where,
    before anything is written or discarded: the earlier record, the tree and the id maps are
    as they were (the coordinator's ruling on delta review 2, REVISED (B) (1))."""
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
