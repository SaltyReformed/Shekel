"""The command line wired end to end: ``record``, ``apply`` and ``prove`` through
:func:`tools.split.split.main` on the sample, in a throwaway repository, with only the arms
that start a process stubbed (the suite runs, pylint and the plan gate; the census runs its
real ``git grep``).  The branch's neutral review (F7) found no test reaching this module:
the proof block printed its fixed sentences even beside a failed arm, and nothing showed it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split import _dynamic as dynamic
from tools.split.conftest import SOURCE, build, layout_of, plan_dict
from tools.split.split import main
from tools.split.test_dynamic import BEFORE_IDS, PACKAGE

#: The importer the sample's split re-points, and the line it rewrites.
IMPORTER, REPOINTED = "tests/test_other.py", 2

#: How each of the sample's tests ends.
OUTCOMES = {**dict.fromkeys(BEFORE_IDS, "passed"), f"{SOURCE}::TestBig::test_c": "skipped",
            f"{SOURCE}::TestBig::test_d": "xfailed", f"{SOURCE}::TestBig::test_e": "xpassed"}


class Stubs:
    """The process arms, answering as a faithful run of the sample would, and what a case
    changes: the importer's pylint messages after the split, the plan gate's verdict, and
    the fixtures the record run set up."""

    def __init__(self, mapping: dict[str, str]) -> None:
        """Answer under ``mapping``, the sample's old -> new id map."""
        self.mapping = mapping
        self.importer_after: list[dict] = []
        self.gate = (0, "3 passed")
        self.setups: list[dict] = []
        self.recorded = False

    def observe(self, _root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
        """The run of ``targets``: the source before, the package or one part after."""
        target = targets[0]
        if target == SOURCE:
            outcomes = OUTCOMES
        else:
            outcomes = {new: OUTCOMES[old] for old, new in self.mapping.items()
                        if new.startswith(target if target.endswith(".py") else target + "/")}
        observed = {"collected": sorted(outcomes) if collect_only else [], "deselected": [],
                    "outcomes": {} if collect_only else outcomes, "exitstatus": 0}
        out.write_text(json.dumps(observed), encoding="utf-8")
        return {**observed, "returncode": 0, "summary": "ok",
                "setups": self.setups if target == SOURCE else [],
                "setups_ran": sorted(outcomes)}

    def pylint(self, _root: Path, paths: list[str]) -> list[dict]:
        """No message anywhere, but the importer's after the split."""
        if paths == [IMPORTER]:
            after, self.recorded = (self.importer_after if self.recorded else []), True
            return after
        return []


@pytest.fixture(name="split")
def _split(tmp_path, monkeypatch):
    """The sample committed to a throwaway repository, the process arms stubbed; a function
    running one command through ``main``, returning its exit status."""
    root = scratch.repository(tmp_path)
    build(root)
    scratch.run(root, "add", "-A")
    scratch.run(root, "commit", "--quiet", "-m", "the sample")
    stubs = Stubs(dynamic.id_map(layout_of(root), BEFORE_IDS))
    monkeypatch.setattr(run, "observe", stubs.observe)
    monkeypatch.setattr(run, "pylint_messages", stubs.pylint)
    monkeypatch.setattr(run, "plan_gate", lambda _root: stubs.gate)
    plan = tmp_path / "test_sample.plan.json"
    plan.write_text(json.dumps(plan_dict()), encoding="utf-8")

    def command(name: str) -> int:
        args = [name, str(plan), "--into", str(tmp_path / "record")]
        if name != "record":
            args += ["--idmap", str(tmp_path / "test_sample.idmap.json")]
        return main(args, root=root)
    command.stubs, command.root = stubs, root
    return command


def applied(split) -> None:
    """Record and apply the sample's split, each proved."""
    assert split("record") == 0
    assert split("apply") == 0


def test_a_proved_split_prints_its_block(split, capsys):
    """Every arm passed: the block is printed whole, and the run says OK."""
    applied(split)
    capsys.readouterr()
    assert split("prove") == 0
    out = capsys.readouterr().out
    assert out.startswith("Proof (python -m tools.split.split prove, plan test_sample.plan.json)")
    assert f"- importers re-pointed: ['{IMPORTER}']" in out
    assert out.rstrip().endswith("prove: OK")


def test_a_failed_arm_prints_no_block(split, capsys):
    """One arm failed: none of the block's sentences is printed, only the failure."""
    applied(split)
    split.stubs.gate = (1, "1 failed")
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert "FAILED: plan gate: 1 failed" in err


def test_an_importer_message_on_an_unchanged_line_is_refused(split, capsys):
    """A message the importer's untouched line did not hold before (the review's F8b)."""
    applied(split)
    split.stubs.importer_after = [{"path": IMPORTER, "line": 5, "symbol": "no-member",
                                   "message": "x"}]
    assert split("prove") == 1
    assert (f"FAILED: importers: {IMPORTER}:5: no-member, which the importer's line 5 did not "
            "hold before the split") in capsys.readouterr().err


def test_an_importer_message_on_its_repointed_import_is_named(split, capsys):
    """The re-point's own message is the commit's edit: named in the block, not refused."""
    applied(split)
    split.stubs.importer_after = [{"path": IMPORTER, "line": REPOINTED, "message": "x",
                                   "symbol": "shekel-private-module-import"}]
    assert split("prove") == 0
    assert (f"on the re-pointed imports: ['{IMPORTER}:{REPOINTED}: "
            "shekel-private-module-import']") in capsys.readouterr().out


def test_an_importer_edited_beyond_its_repoint_is_refused(split, capsys):
    """A line moved under the importer's messages would match the wrong origin: refused."""
    applied(split)
    path = split.root / IMPORTER
    path.write_text("# a line the split did not write\n" + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert split("prove") == 1
    assert (f"FAILED: importers: {IMPORTER}:{REPOINTED} is not its re-pointed import"
            in capsys.readouterr().err)


def test_an_importer_edited_on_another_line_is_refused(split, capsys):
    """The re-pointed import intact, another line changed: the recorded text is the check."""
    applied(split)
    path = split.root / IMPORTER
    path.write_text(path.read_text(encoding="utf-8").replace("assert _make(3) == 3",
                                                             "assert _make(3) == 4"),
                    encoding="utf-8")
    assert split("prove") == 1
    assert (f"FAILED: importers: {IMPORTER} changed beyond its re-pointed imports since it was "
            "recorded" in capsys.readouterr().err)


def test_record_refuses_a_fixture_the_split_would_cut(split, capsys):
    """A parent conftest's module-scoped fixture set up for the source's tests (fork F)."""
    split.stubs.setups = [{"argname": "area", "scope": "module", "baseid": "tests",
                           "file": str(split.root / "tests/conftest.py"), "by": SOURCE}]
    assert split("record") == 1
    assert "FAILED: fixture area (scope module" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--into", "--idmap"])
def test_a_record_inside_the_repository_is_refused(split, capsys, flag):
    """The census reads untracked files, and a record names the old module throughout."""
    plan = split.root.parent / "test_sample.plan.json"
    args = ["prove", str(plan), "--into", str(split.root.parent / "record"),
            "--idmap", str(split.root.parent / "map.json")]
    args[args.index(flag) + 1] = str(split.root / "inside")
    with pytest.raises(SystemExit) as caught:
        main(args, root=split.root)
    assert caught.value.code == 2
    assert "must lie outside the repository" in capsys.readouterr().err


def test_the_census_lists_the_untracked_package_through_main(split, capsys):
    """The package is untracked when ``prove`` runs; the real census still reads it, and the
    plan text naming nothing old leaves every hit the tool's own provenance."""
    applied(split)
    capsys.readouterr()
    assert split("prove") == 0
    out = capsys.readouterr().out
    assert "- census of the old module's name: 0 live, 0 archived, 5 the package's own " \
           "provenance (not counted)" in out
    assert not scratch.run(split.root, "ls-files", PACKAGE)
