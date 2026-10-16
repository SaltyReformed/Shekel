"""The record's own life through :func:`tools.split.split.main`: each run of record or prove
writes into a directory made for it, and ``meta.json``, the token a complete record writes
last, names its run's directory and replaces the last record's token only then.  So a record
that fails part way or cannot start leaves the last record whole, a pytest that outlived a
killed record or prove writes where nothing reads, a prove's block names the directories its
evidence is in, a directory no record completed in is refused by name, and a directory
holding an applied split's record is never recorded into."""
from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split._observe import OBSERVE_ENV, setups_path
from tools.split._run import observe, pylint_messages
from tools.split._source import Refusal
from tools.split.conftest import (
    EMPTY,
    RECORD_STEPS,
    SAMPLE,
    SOURCE,
    applied,
    committed,
    cut_fixture,
    held,
    only_on_path,
    plan_of,
    tree,
)
from tools.split.split import _AFTER_RUN, _BEFORE_RUN, _META, _SOURCE, Context, _save


def _stopped_at(step: str):
    """A stub stopping the run at ``step`` (one of :data:`RECORD_STEPS`), as a process that
    did not observe would, and what the refusal says."""
    def stop(split, _monkeypatch) -> None:
        split.stubs.stop_at = step
    return stop, f"REFUSED: stopped at {step}"


def _no_pylint(split, monkeypatch) -> None:
    """No pylint on ``PATH``, the real arm starting it: the OSError main refuses."""
    monkeypatch.setattr(run, "pylint_messages", pylint_messages)
    only_on_path(split.root.parent, monkeypatch, "git")


#: Each point at which a record fails once its run has begun, and what its refusal says: a
#: process that did not observe, and a program that cannot start (the deleted start checks'
#: case, the coordinator's ruling on 3C-2's prove fork, (f), 2026-10-16).
FAILURES = {**{step: _stopped_at(step) for step in RECORD_STEPS},
            "pylint not on PATH": (_no_pylint,
                                   "REFUSED: [Errno 2] No such file or directory: 'pylint'")}


def _through_the_wrapper(split, monkeypatch, during) -> None:
    """Every run through the real :func:`tools.split._run.observe`, its wrapper a stand-in:
    the stubs write what they observe where the observer plugin would, and then ``during`` is
    called with the observation's path while the wrapper still runs, as a pytest that
    outlived an earlier run writes, or a kill stops this one."""
    def wrapper(args, root, env):
        out = Path(env[OBSERVE_ENV])
        split.stubs.observe(root, args[1:args.index("-p")], out, "--collect-only" in args)
        during(out)
        return 0, b""
    monkeypatch.setattr(run, "observe", observe)
    monkeypatch.setattr(run, "_wrapper_run", wrapper)


@pytest.mark.parametrize("moved", [False, True], ids=["HEAD-unmoved", "the-source-edited"])
@pytest.mark.parametrize("failure", FAILURES)
def test_a_record_that_fails_part_way_leaves_the_last_record_whole(split, capsys, monkeypatch,
                                                                   failure, moved):
    """A complete record, then a second into the same directory failing at ``failure``: the
    first record's token and every file of its run are as they were, because a token is
    replaced only once a run completes (the coordinator's ruling on delta review 3's N-2,
    2026-10-16: the second record deleted the first before its runs, so a wrapper that could
    not run cost the last record).  With HEAD where it was, the last record is applied and
    proved.  With the source edited and committed since, apply and prove refuse it by HEAD,
    with the tree as it was, so its observations are never read as the edited source's (the
    delta review's H-A, which a token deleted first once met)."""
    assert split("record") == 0
    before, taken = tree(split.into), scratch.run(split.root, "rev-parse", "HEAD")
    if moved:
        source = split.root / SOURCE
        source.write_text(source.read_text(encoding="utf-8").replace(
            '"""One."""', '"""One, edited."""'), encoding="utf-8")
        committed(split.root, "the source edited")
    fails, said = FAILURES[failure]
    capsys.readouterr()
    with monkeypatch.context() as patched:
        fails(split, patched)
        assert split("record") == 1
    assert said in capsys.readouterr().err
    assert before.items() <= tree(split.into).items()
    split.stubs.stop_at = None
    if not moved:
        assert split("apply") == 0
        assert split("prove") == 0
        return
    now, unchanged = scratch.run(split.root, "rev-parse", "HEAD"), tree(split.root)
    for command in ("apply", "prove"):
        assert split(command) == 1
        assert (f"REFUSED: HEAD is {now[:9]}, and the record was taken at {taken[:9]}; "
                in capsys.readouterr().err)
    assert tree(split.root) == unchanged
    assert not split.idmap.exists()


def test_an_observation_a_killed_record_outlived_is_never_read(split, monkeypatch):
    """A record stopped part way, as ``SIGKILL`` leaves one, whose pytest outlived it and,
    while the next record's wrapper runs, writes into that run's directory its observation
    and a worker's fixture setups, a worker the next run has none of (the orphan race,
    measured 2026-10-15): the next record never reads them, because each run writes into a
    directory made for it, so it completes and its split is applied (the coordinator's
    rulings on 3B-2b's condition (c), 2026-10-15, and on 3C-2's prove fork, (d)).  What the
    orphan writes fails the verdict twice over, nothing observed and a fixture the split
    would cut, and lands after the next run began, where deleting the files first could not
    reach it."""
    seen, later = [], []

    def during(out):
        seen.append(out)
        if later and out.name == _BEFORE_RUN:
            orphan = seen[0].parent / _BEFORE_RUN
            orphan.write_text(json.dumps(EMPTY), encoding="utf-8")
            setups_path(orphan, "gw0").write_text(json.dumps(
                {"setups": [cut_fixture(split.root)], "ran": []}), encoding="utf-8")
    _through_the_wrapper(split, monkeypatch, during)
    split.stubs.stop_at = "the run"
    assert split("record") == 1
    split.stubs.stop_at = None
    later.append(True)
    assert split("record") == 0
    assert split("apply") == 0


def test_an_observation_a_killed_prove_outlived_is_never_read(split, monkeypatch):
    """A prove stopped in its run of the split, whose pytest outlived it and writes failing
    outcomes into that prove's ``after_run.json`` while the next prove's wrapper runs: the
    next prove never reads them, because each prove writes into a directory made for it,
    so it proves the split (the coordinator's ruling on 3C-2's prove fork, (1) and (d),
    2026-10-16: at fixed paths, deleted before each run, the orphan's write landed after the
    deletion and was read as the next prove's)."""
    applied(split)
    seen, later = [], []

    def during(out):
        seen.append(out)
        if out.name == _AFTER_RUN:
            if not later:
                raise run.RunError("killed in its run of the split")
            (seen[0].parent / out.name).write_text(json.dumps(EMPTY), encoding="utf-8")
    _through_the_wrapper(split, monkeypatch, during)
    assert split("prove") == 1
    later.append(True)
    assert split("prove") == 0


def test_each_prove_writes_into_a_directory_of_its_own_and_names_it(split, capsys):
    """Two proves beside one record: each writes every file into a directory made for it, its
    name the command's, and its block names the record's run directory and its own, each
    relative to ``--into``; the revealed pairs are listed there, and nothing beside the id
    map (the coordinator's ruling on 3C-2's prove fork, (a) to (c), 2026-10-16)."""
    applied(split)
    record_run = held(split, _SOURCE).parent.name
    made = []
    for _ in range(2):
        before = {path.name for path in split.into.iterdir()}
        capsys.readouterr()
        assert split("prove") == 0
        out = capsys.readouterr().out
        (prove_run,) = {path.name for path in split.into.iterdir()} - before
        assert (f"- evidence, in --into: the record's run {record_run}, this prove's "
                f"{prove_run}\n") in out
        assert f"listed in {prove_run}/test_sample.r0801.txt)" in out
        assert (split.into / prove_run / "test_sample.r0801.txt").is_file()
        made.append(prove_run)
    assert record_run.startswith("record-") and all(m.startswith("prove-") for m in made)
    assert sorted(path.name for path in split.into.iterdir()) == sorted(
        [_META, "idmap.json", record_run, *made])
    assert sorted(path.name for path in split.idmap.parent.iterdir()
                  if path.name.startswith("test_sample.")) == ["test_sample.idmap.json",
                                                              "test_sample.plan.json"]


@pytest.mark.parametrize("command", ["apply", "prove"])
def test_a_directory_holding_no_record_is_refused_by_name(split, capsys, command):
    """Nothing was ever recorded there: refused, naming the directory, not a traceback."""
    assert split(command) == 1
    assert (f"REFUSED: {split.into}: no complete record; record again"
            in capsys.readouterr().err)


@pytest.mark.parametrize("last", [False, True], ids=["no-last-token", "a-last-token"])
def test_a_token_stopped_mid_write_leaves_the_last_one(tmp_path, monkeypatch, last):
    """The token is written beside its name and renamed onto it, so a record stopped while
    writing it (here, a full disk half way through) leaves the last record's token byte for
    byte, or none where there was none: never a part of a new one that ``apply`` would read
    as the record's."""
    ctx = Context(plan=plan_of(), root=tmp_path, into=tmp_path)
    token, kept = tmp_path / _META, {"plan": ctx.plan.sha256, "run": "record-last"}
    if last:
        _save(token, kept)
    before = token.read_bytes() if last else None
    real = Path.write_text

    def stopped(self, data, *args, **kwargs):
        if self.name.startswith(_META):
            real(self, data[:len(data) // 2], *args, **kwargs)
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, data, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", stopped)
    with pytest.raises(OSError):
        _save(token, {"plan": ctx.plan.sha256, "run": "record-next"})
    monkeypatch.undo()
    if last:
        assert token.read_bytes() == before
        assert ctx.bound().meta == kept
        return
    assert not token.exists()
    with pytest.raises(Refusal, match="no complete record; record again"):
        ctx.bound()


@pytest.mark.parametrize("reverted", [False, True], ids=["in-place", "reverted-by-hand"])
def test_a_record_whose_split_is_applied_is_never_replaced(split, capsys, reverted):
    """``record`` into the directory of an applied split is refused before it writes
    anything, whether the split is in the tree or was reverted by hand, and the applied
    record's files are byte-identical after: that record is the evidence the split's commit
    cites, and a later record is taken into a new ``--into`` (the coordinator's rulings on
    delta review 2's Medium-1, 2026-10-15, and on 3C-2's question, 2026-10-16).  Reverted by
    hand, the tree is HEAD's again, so this refusal alone stops a record that would
    complete."""
    applied(split)
    if reverted:
        scratch.run(split.root, "checkout", "--quiet", "--", ".")
        scratch.run(split.root, "clean", "-fdq")
        assert (split.root / SOURCE).is_file()
    before = tree(split.into)
    capsys.readouterr()
    assert split("record") == 1
    assert (f"REFUSED: {split.into} holds a record whose split is applied; record into a new "
            "--into") in capsys.readouterr().err
    assert tree(split.into) == before
    if not reverted:
        assert split("prove") == 0


def _uncompilable(split, _monkeypatch, _tmp_path) -> None:
    """The source no longer compiles (committed: record refuses a tree that is not HEAD's
    before it reads the source)."""
    (split.root / SOURCE).write_text(SAMPLE + "\ndef (\n", encoding="utf-8")
    committed(split.root, "the source broken")


def _into_not_writable(split, _monkeypatch, _tmp_path) -> None:
    """The record directory cannot be written in."""
    split.into.chmod(0o500)


def _no_head(split, _monkeypatch, _tmp_path) -> None:
    """The branch has no commit for ``record`` to name."""
    scratch.run(split.root, "update-ref", "-d", "HEAD")


#: Each way a second record cannot start, and what its refusal says.
CANNOT_START = {
    "the source no longer compiles": (_uncompilable, "Python does not compile it"),
    "the record directory is not writable": (_into_not_writable,
                                             "REFUSED: [Errno 13] Permission denied: "),
    "HEAD names no commit": (_no_head, "git rev-parse HEAD: fatal: "),
}


@pytest.mark.parametrize("case", CANNOT_START)
def test_a_record_that_cannot_start_leaves_the_last_record_as_it_was(split, capsys,
                                                                     monkeypatch, tmp_path,
                                                                     case):
    """A second record that cannot start refuses, and the first record's files are untouched:
    refused before its run writes anything or, in a directory it cannot write in, when it
    makes its run's directory (delta review 2's Medium-1, whose order of checks, each before a
    discard, the run's own directory made unnecessary)."""
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
