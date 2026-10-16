"""The record's own life through :func:`tools.split.split.main`: ``meta.json`` is the token a
complete run writes last, so a record that stops part way leaves none that apply or prove
would read as the edited source's, a directory holding none is refused by name, a record that
cannot start leaves the last one as it was, and a directory holding an applied split's record
is never recorded into."""
from __future__ import annotations

import errno
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split._model import parse_plan
from tools.split._source import Refusal
from tools.split.conftest import (
    RECORD_STEPS,
    SAMPLE,
    SOURCE,
    applied,
    committed,
    only_on_path,
    plan_dict,
    tree,
)
from tools.split.split import _META, Context


@pytest.mark.parametrize("step", RECORD_STEPS)
def test_a_record_that_stops_part_way_leaves_no_record(split, capsys, step):
    """A complete record, then the source edited (and committed, as record requires) and
    recorded again into the same directory, stopped at ``step``: no token, so apply refuses
    with the tree as it was, and prove refuses (the delta review's H-A: the second record
    wrote its token first, and apply and prove read the first record's observations as the
    edited source's)."""
    assert split("record") == 0
    source = split.root / SOURCE
    source.write_text(source.read_text(encoding="utf-8").replace('"""One."""',
                                                               '"""One, edited."""'),
                      encoding="utf-8")
    committed(split.root, "the source edited")
    split.stubs.stop_at = step
    capsys.readouterr()
    assert split("record") == 1
    assert f"REFUSED: stopped at {step}" in capsys.readouterr().err
    assert not (split.into / _META).exists()
    before = tree(split.root)
    assert split("apply") == 1
    assert (f"REFUSED: {split.into}: no complete record; record again"
            in capsys.readouterr().err)
    assert tree(split.root) == before
    assert not split.idmap.exists()
    assert split("prove") == 1
    assert (f"REFUSED: {split.into}: no complete record; record again"
            in capsys.readouterr().err)


@pytest.mark.parametrize("command", ["apply", "prove"])
def test_a_directory_holding_no_record_is_refused_by_name(split, capsys, command):
    """Nothing was ever recorded there: refused, naming the directory, not a traceback."""
    assert split(command) == 1
    assert (f"REFUSED: {split.into}: no complete record; record again"
            in capsys.readouterr().err)


def test_a_token_stopped_mid_write_is_no_token(tmp_path, monkeypatch):
    """The token is written beside its name and renamed onto it, so a record stopped while
    writing it (here, a full disk half way through) leaves no ``meta.json``, never a part
    of one that ``apply`` would read as the record's."""
    ctx = Context(plan=parse_plan(plan_dict()), root=tmp_path, into=tmp_path)
    real = Path.write_text

    def stopped(self, data, *args, **kwargs):
        if self.name.startswith(_META):
            real(self, data[:len(data) // 2], *args, **kwargs)
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, data, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", stopped)
    with pytest.raises(OSError):
        ctx.save(_META, {"source": SOURCE, "sha256": "0" * 64})
    monkeypatch.undo()
    assert not (tmp_path / _META).exists()
    with pytest.raises(Refusal, match="no complete record; record again"):
        ctx.meta()


@pytest.mark.parametrize("reverted", [False, True], ids=["in-place", "reverted-by-hand"])
def test_a_record_whose_split_is_applied_is_never_discarded(split, capsys, reverted):
    """``record`` into the directory of an applied split is refused before it discards
    anything, whether the split is in the tree or was reverted by hand (delta review 2's
    Medium-1: a second record deleted the token and the source, and prove then asked for a
    record no one could take)."""
    applied(split)
    if reverted:
        scratch.run(split.root, "checkout", "--quiet", "--", ".")
        scratch.run(split.root, "clean", "-fdq")
        assert (split.root / SOURCE).is_file()
    before = tree(split.into)
    capsys.readouterr()
    assert split("record") == 1
    assert (f"REFUSED: {split.into} holds a record whose split is applied; record into "
            "another directory") in capsys.readouterr().err
    assert tree(split.into) == before
    if not reverted:
        assert split("prove") == 0


def _uncompilable(split, _monkeypatch, _tmp_path) -> None:
    """The source no longer compiles (committed: record refuses a tree that is not HEAD's
    before it reads the source)."""
    (split.root / SOURCE).write_text(SAMPLE + "\ndef (\n", encoding="utf-8")
    committed(split.root, "the source broken")


def _no_pylint(_split, monkeypatch, tmp_path) -> None:
    """No pylint on ``PATH``."""
    only_on_path(tmp_path, monkeypatch, "git")


def _wrapper_not_executable(split, _monkeypatch, _tmp_path) -> None:
    """The suite wrapper is there, and cannot be started (committed, as above)."""
    (split.root / run.WRAPPER).chmod(0o644)
    committed(split.root, "the wrapper not executable")


def _into_not_writable(split, _monkeypatch, _tmp_path) -> None:
    """The record directory cannot be written in."""
    split.into.chmod(0o500)


def _no_head(split, _monkeypatch, _tmp_path) -> None:
    """The branch has no commit for ``record`` to name."""
    scratch.run(split.root, "update-ref", "-d", "HEAD")


#: Each way a second record cannot start, and what its refusal says.
CANNOT_START = {
    "the source no longer compiles": (_uncompilable, "Python does not compile it"),
    "no pylint": (_no_pylint, "record cannot start ['pylint']"),
    "the wrapper cannot start": (_wrapper_not_executable,
                                 "record cannot start ['./scripts/test.sh']"),
    "the record directory is not writable": (
        _into_not_writable, "not a directory this process can write files in"),
    "HEAD names no commit": (_no_head, "git rev-parse HEAD: fatal: "),
}


@pytest.mark.parametrize("case", CANNOT_START)
def test_a_record_that_cannot_start_leaves_the_last_record_as_it_was(split, capsys,
                                                                     monkeypatch, tmp_path,
                                                                     case):
    """Every check that writes nothing runs before the discard (delta review 2's Medium-1):
    a second record that cannot start refuses, and the first record's files are untouched."""
    assert split("record") == 0
    before = tree(split.into)
    breaks, said = CANNOT_START[case]
    breaks(split, monkeypatch, tmp_path)
    capsys.readouterr()
    try:
        assert split("record") == 1
        assert said in capsys.readouterr().err
        assert tree(split.into) == before
    finally:
        split.into.chmod(0o700)
