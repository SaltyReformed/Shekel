"""The command line wired end to end: ``record``, ``apply`` and ``prove`` through
:func:`tools.split.split.main` on the sample, in a throwaway repository, with only the arms
that start a process stubbed (the suite runs, pylint and the plan gate; the census runs its
real ``git grep``).  The branch's neutral review (F7) found no test reaching this module:
the proof block printed its fixed sentences even beside a failed arm, and nothing showed it.
The record's own life (:mod:`tools.split.test_record`) and the state a record is judged in
(:mod:`tools.split.test_tree_state`) have modules of their own."""
from __future__ import annotations

import errno
import json
import os
import signal
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _run as run
from tools.split import split as cli
from tools.split._observe import LONE, setups_path
from tools.split._run import pylint_messages
from tools.split.conftest import (
    IMPORTER,
    OUTCOMES,
    PACKAGE,
    REPOINTED,
    SOURCE,
    applied,
    committed,
    everything,
    only_on_path,
    plan_dict,
    tree,
)
from tools.split.split import _BEFORE_RUN, _META, main

#: The shape every run had in the delta review's M-A: nothing collected, nothing run, and
#: pytest interrupted.
EMPTY = {"collected": [], "outcomes": {}, "exitstatus": 2}


def cut_fixture(root: Path) -> dict:
    """A parent conftest's module-scoped fixture set up for the source's tests: its one
    instance per module the split would cut into one per part (fork F)."""
    return {"argname": "area", "scope": "module", "baseid": "tests",
            "file": str(root / "tests/conftest.py"), "by": SOURCE}


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
    split.stubs.setups = [cut_fixture(split.root)]
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


def test_a_plan_inside_the_repository_is_refused(split, capsys):
    """A plan names the old module throughout, as a record does, and the census reads every
    file git does not ignore (the review's L9): refused before anything is recorded."""
    plan = split.root / "test_sample.plan.json"
    plan.write_text(json.dumps(plan_dict()), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["record", str(plan), "--into", str(split.into)], root=split.root)
    assert caught.value.code == 2
    assert "must lie outside the repository" in capsys.readouterr().err
    assert not split.into.exists()


@pytest.mark.parametrize("command", ["apply", "prove"])
def test_apply_and_prove_without_a_map_exit_two(split, capsys, command):
    """The command line's own refusal, before anything runs, exits 2 as the module says (the
    delta review's L-A item 6: it said 1)."""
    plan = split.root.parent / "test_sample.plan.json"
    with pytest.raises(SystemExit) as caught:
        main([command, str(plan), "--into", str(split.into)], root=split.root)
    assert caught.value.code == 2
    assert "apply and prove need --idmap" in capsys.readouterr().err
    assert not split.into.exists()


def test_apply_refuses_a_record_that_failed(split, capsys):
    """The record's verdict gates apply (the review's H1: record refused a fixture, and apply
    exited 0 and wrote the split anyway); nothing is written."""
    split.stubs.setups = [cut_fixture(split.root)]
    assert split("record") == 1
    before = tree(split.root)
    capsys.readouterr()
    assert split("apply") == 1
    assert ("REFUSED: the record did not pass (1 failure(s), first: fixture area (scope module"
            in capsys.readouterr().err)
    assert tree(split.root) == before
    assert not split.idmap.exists()


def test_prove_judges_the_record_again(split, capsys):
    """A record whose verdict fails reaches prove (made to fail after apply, here): prove fails
    it as an arm, prefixed ``record:``, and prints no block (the review's H1)."""
    applied(split)
    held = setups_path(split.into / _BEFORE_RUN, LONE)
    observed = json.loads(held.read_text(encoding="utf-8"))
    observed["setups"] = [cut_fixture(split.root)]
    held.write_text(json.dumps(observed), encoding="utf-8")
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert "FAILED: record: fixture area (scope module" in err


def test_the_block_counts_the_fixtures_the_record_set_up(split, capsys):
    """A parent conftest's session fixture reaches every part as it reached the source:
    accepted, and counted on the block's fixtures line (which the review's H1 added)."""
    split.stubs.setups = [{**cut_fixture(split.root), "argname": "app", "scope": "session"}]
    applied(split)
    capsys.readouterr()
    assert split("prove") == 0
    assert "- fixtures: 1 set up by the source's tests; none scoped" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["an importer added", "an importer edited"])
def test_apply_refuses_importers_the_record_did_not_take(split, capsys, change):
    """apply refuses, before any write, importers record did not fingerprint and lint (the
    review's M1: a module importing the source added after record was re-pointed and never
    linted).  Since REVISED (B) the change meets the clean-tree refusal first, which names
    it; the importer re-check behind it can no longer fire through main for a tracked source
    (the coordinator's ruling, 3B-2b: 3C deletes that re-check, and this case with it)."""
    assert split("record") == 0
    if change == "an importer added":
        (split.root / "tests/test_late.py").write_text(
            "from tests.test_area.test_sample import _make\n", encoding="utf-8")
    else:
        path = split.root / IMPORTER
        path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    before = tree(split.root)
    capsys.readouterr()
    assert split("apply") == 1
    named = "tests/test_late.py" if change == "an importer added" else IMPORTER
    assert (f"REFUSED: the working tree differs from HEAD at 1 path(s), e.g. ['{named}']"
            in capsys.readouterr().err)
    assert tree(split.root) == before
    assert not split.idmap.exists()


def test_importers_are_found_wherever_git_does_not_ignore_them(split, capsys):
    """Every ``.py`` git does not ignore (the coordinator's ruling on review fork L7): a
    tracked module outside the four directories the walk once read is re-pointed, recorded
    and proved; an ignored one is no importer and is left as it was.  (An untracked one was a
    case here; since REVISED (B) it is a tree record refuses, by
    ``test_a_tree_that_is_not_heads_is_refused``.)"""
    text = "from tests.test_area.test_sample import _make\n"
    found = ["migrations/check.py", IMPORTER]
    (split.root / ".gitignore").write_text("build/\n", encoding="utf-8")
    for path in ("migrations/check.py", "build/stale.py"):
        (split.root / path).parent.mkdir(exist_ok=True)
        (split.root / path).write_text(text, encoding="utf-8")
    committed(split.root, "a tracked importer, an ignored one")
    applied(split)
    assert sorted(json.loads((split.into / _META).read_text(encoding="utf-8"))["importers"]) \
        == found
    for path in found:
        assert "from tests.test_area.test_sample._helpers import _make" in (
            split.root / path).read_text(encoding="utf-8")
    assert (split.root / "build/stale.py").read_text(encoding="utf-8") == text
    capsys.readouterr()
    assert split("prove") == 0
    assert f"- importers re-pointed: {found}" in capsys.readouterr().out


def test_a_message_on_a_multi_line_imports_last_line_is_named(split, capsys):
    """Every line of a re-pointed statement is the re-point's own (the review's L3): a message
    on the closing line of a parenthesised import is named in the block, not refused."""
    path = split.root / IMPORTER
    path.write_text(path.read_text(encoding="utf-8").replace(
        "import _make\n", "import (\n    _make,\n)\n"), encoding="utf-8")
    committed(split.root, "a parenthesised import")
    applied(split)
    last = REPOINTED + 2
    split.stubs.importer_after = [{"path": IMPORTER, "line": last, "message": "x",
                                   "symbol": "shekel-private-module-import"}]
    capsys.readouterr()
    assert split("prove") == 0
    assert (f"on the re-pointed imports: ['{IMPORTER}:{last}: shekel-private-module-import']"
            in capsys.readouterr().out)


def test_an_importer_with_carriage_returns_is_refused(split, capsys):
    """The writer reads and writes text, so it would rewrite an importer's CRLF line endings
    along with its import (the review's L6): refused at record."""
    path = split.root / IMPORTER
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    committed(split.root, "CRLF line endings")
    assert split("record") == 1
    assert f"{path}: a carriage return in its line endings" in capsys.readouterr().err


def test_carriage_returns_in_a_module_the_writer_leaves_alone_are_accepted(split):
    """Only a module the writer edits is refused: one that merely names the source keeps its
    line endings, because nothing rewrites it."""
    (split.root / "tests/test_notes.py").write_bytes(b'"""Names test_sample in prose."""\r\n')
    committed(split.root, "a module naming the source in prose")
    assert split("record") == 0


def test_a_record_whose_runs_observed_nothing_is_refused_at_record_and_apply(split, capsys):
    """The delta review's M-A shape (every run collected nothing, pytest interrupted):
    record fails it, and apply refuses it with nothing written."""
    split.stubs.changed = {"source collect": EMPTY, "source run": EMPTY}
    assert split("record") == 1
    err = capsys.readouterr().err
    assert ("FAILED: the source's collection: pytest exit status 2, not a session run to its "
            "end") in err
    assert ("FAILED: the source's run: no id for 7 test(s) of the source, e.g. "
            "['TestFirst', 'test_top_level', 'TestBig::test_a']") in err
    before = tree(split.root)
    assert split("apply") == 1
    assert "REFUSED: the record did not pass (4 failure(s)" in capsys.readouterr().err
    assert tree(split.root) == before
    assert not split.idmap.exists()


@pytest.mark.parametrize("status,code", [(1, 0), (3, 1), (5, 1)])
def test_a_record_is_refused_unless_pytest_ran_its_session_to_the_end(split, capsys, status,
                                                                     code):
    """Every test collected and run as before, the run's exit status alone decides: failing
    tests (1) are still an observation of the tests; an internal error (3), or pytest
    reporting no tests (5), is not."""
    split.stubs.changed = {"source run": {"exitstatus": status}}
    assert split("record") == code
    refused = (f"FAILED: the source's run: pytest exit status {status}, not a session run to "
               "its end")
    assert (refused in capsys.readouterr().err) == bool(code)


@pytest.mark.parametrize("dropped,named", [("::TestFirst::", "TestFirst"),
                                           ("::TestBig::test_c", "TestBig::test_c")])
def test_a_record_is_refused_when_a_test_of_the_source_yields_no_id(split, capsys, dropped,
                                                                    named):
    """pytest ran to its end and the collection agrees with the run, but neither holds a
    test the source holds (a whole class, or one test of a split class): refused, because
    the proof would never compare it."""
    kept = {nodeid: outcome for nodeid, outcome in OUTCOMES.items() if dropped not in nodeid}
    split.stubs.changed = {"source collect": {"collected": sorted(kept)},
                           "source run": {"outcomes": kept}}
    assert split("record") == 1
    assert (f"FAILED: the source's collection: no id for 1 test(s) of the source, e.g. "
            f"['{named}']") in capsys.readouterr().err


def test_prove_refuses_a_split_whose_runs_observed_nothing(split, capsys):
    """The M-A shape after the split: every run of the package and of each part empty."""
    applied(split)
    split.stubs.changed = dict.fromkeys(("split collect", "split run", "alone run"), EMPTY)
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert ("FAILED: the split's collection: no id for 7 test(s) of the source, e.g. "
            "['TestFirst', 'test_top_level', 'TestBig::test_a']") in err
    assert ("FAILED: test_big_late alone: no id for 3 test(s) of the source, e.g. "
            "['TestBig::test_c', 'TestBig::test_d', 'TestBig::test_e']") in err


@pytest.mark.parametrize("kind,what", [("split collect", "the split's collection"),
                                       ("split run", "the split's run"),
                                       ("alone run", "test_first alone")])
def test_prove_refuses_a_run_of_the_split_that_did_not_run_to_its_end(split, capsys, kind,
                                                                      what):
    """Every id observed and every outcome as before, but pytest did not run the session to
    its end: only the run's own exit status shows it, and prove fails it."""
    applied(split)
    split.stubs.changed = {kind: {"exitstatus": 3}}
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert f"FAILED: {what}: pytest exit status 3, not a session run to its end" in err


class TestAnOSErrorIsARefusal:
    """``main`` catches :class:`OSError` once, so a file it cannot write or a program it cannot
    start is a refusal naming it, exit 1 as documented, never a traceback (the coordinator's
    ruling on commit 3b's (A))."""

    def test_pylint_not_installed(self, split, capsys, monkeypatch, tmp_path):
        """record checks it can start pylint before it discards anything: refused, naming
        it, no record (delta review 2's Medium-1 moved this before the discard; it was an
        OSError from the start)."""
        monkeypatch.setattr(run, "pylint_messages", pylint_messages)
        only_on_path(tmp_path, monkeypatch, "git")
        assert split("record") == 1
        assert ("REFUSED: record cannot start ['pylint']; nothing is recorded"
                in capsys.readouterr().err)
        assert not (split.into / _META).exists()

    def test_pylint_not_installed_at_prove(self, split, capsys, monkeypatch, tmp_path):
        """prove reaches pylint with no pylint to start: the OSError is refused by main,
        naming it, never a traceback (the walk that reaches main's catch, now that record
        checks first)."""
        applied(split)
        monkeypatch.setattr(run, "pylint_messages", pylint_messages)
        only_on_path(tmp_path, monkeypatch, "git")
        capsys.readouterr()
        assert split("prove") == 1
        assert ("REFUSED: [Errno 2] No such file or directory: 'pylint'"
                in capsys.readouterr().err)

    def test_an_unwritable_record_directory(self, split, capsys):
        """record checks it can write in ``--into`` before it discards anything: refused,
        naming the directory (delta review 2's Medium-1; it was the copy's OSError)."""
        split.into.mkdir()
        split.into.chmod(0o500)
        try:
            assert split("record") == 1
        finally:
            split.into.chmod(0o700)
        assert (f"REFUSED: {split.into}: not a directory this process can write files in; "
                "nothing is recorded" in capsys.readouterr().err)
        assert not (split.into / _META).exists()


def test_apply_twice_is_refused_by_name(split, capsys):
    """The first apply deleted the source: the second is refused, as already applied, before
    anything reads it (the delta review's L-B: it raised FileNotFoundError hashing the
    deleted file; delta review 2's P2: the refusal said only that it MAY be applied)."""
    applied(split)
    before = everything(split)
    capsys.readouterr()
    assert split("apply") == 1
    assert (f"REFUSED: {split.into}: already applied (it holds the id map an apply keeps only "
            "when it succeeded); nothing is applied" in capsys.readouterr().err)
    assert everything(split) == before


def test_a_failed_git_command_is_a_refusal(split, capsys):
    """A repository whose branch has no commit: record cannot name the commit it is taken at,
    and git's error is a refusal (the delta review's L-B: GitError was uncaught)."""
    scratch.run(split.root, "update-ref", "-d", "HEAD")
    assert split("record") == 1
    assert "REFUSED: git rev-parse HEAD: fatal: " in capsys.readouterr().err
    assert not (split.into / _META).exists()


#: Each write of an apply a case fails, as ``(file, the Path method that fails on it)``.
APPLY_WRITES = {
    "the record's id map": (lambda split: split.into / "idmap.json", "write_text"),
    "the source's deletion": (lambda split: split.root / SOURCE, "unlink"),
    "an importer's rewrite": (lambda split: split.root / IMPORTER, "write_text"),
}


@pytest.mark.parametrize("step", APPLY_WRITES)
@pytest.mark.parametrize("earlier", [False, True], ids=["no-map-before", "a-map-before"])
def test_an_apply_that_fails_part_way_changes_nothing_anywhere(split, capsys, monkeypatch,
                                                              step, earlier):
    """An OSError at ``step`` undoes THE APPLY, not the tree alone (the coordinator's ruling
    on commit 3b's (A)): the tree, the record directory and the ``--idmap`` file are as they
    were, a map an earlier apply left there put back and a new one removed.  The id maps
    are written last, after the proof, so the first case fails with the package, the
    source's deletion, the importer's rewrite and ``--idmap`` written; the others before
    either map."""
    assert split("record") == 0
    if earlier:
        split.idmap.write_bytes(b'{"an": "earlier apply\'s map"}\n')
    before = everything(split)
    target, method = APPLY_WRITES[step][0](split), APPLY_WRITES[step][1]
    real = getattr(Path, method)

    def full(self, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, method, full)
    capsys.readouterr()
    assert split("apply") == 1
    monkeypatch.undo()
    assert ("every file it created is removed and every file it changed put back"
            in capsys.readouterr().err)
    assert everything(split) == before


#: What a write that undid itself says.
UNDONE = "every file it created is removed and every file it changed put back"

#: The signals main raises as an exception, by name.
SIGNALS = {"SIGTERM": signal.SIGTERM, "SIGHUP": signal.SIGHUP}


class TestTheApplyIsOneUnit:
    """The writes and the static proof of what they wrote are ONE unit: a proof that fails, an
    interrupt, or a check that fails before the first write leaves everything as it was (the
    coordinator's ruling on commit 3b's (A), gaps closed: the tree never holds an unproved
    split)."""

    @staticmethod
    def proof_seeing(seen: list, answer):
        """A static proof that takes the tree as it finds it into ``seen``, then answers by
        calling ``answer``."""
        def proof(_layout, root, _rules):
            seen.append(tree(root))
            return answer()
        return proof

    def test_a_static_proof_that_fails_after_a_complete_write(self, split, capsys,
                                                              monkeypatch):
        """The proof runs on the split as written, finds it wrong, and the refusal names
        every failure; the write is undone (delta review 2's gap 3: apply returned the
        failures with the split left on disk)."""
        assert split("record") == 0
        before, seen = everything(split), []
        monkeypatch.setattr(cli, "prove_static", self.proof_seeing(
            seen, lambda: ["a failure the stub reports", "and a second"]))
        capsys.readouterr()
        assert split("apply") == 1
        assert ("REFUSED: the static proof failed on the split as written (2 failure(s): a "
                f"failure the stub reports; and a second); {UNDONE}") in capsys.readouterr().err
        assert f"{PACKAGE}/test_first.py" in seen[0] and SOURCE not in seen[0]
        assert everything(split) == before

    def test_an_interrupt_while_the_proof_runs(self, split, monkeypatch):
        """Ctrl-C inside the unit, after a complete write: undone, then the interrupt goes on
        as it was, with a note saying what the undo did."""
        assert split("record") == 0
        before, seen = everything(split), []

        def interrupted():
            raise KeyboardInterrupt
        monkeypatch.setattr(cli, "prove_static", self.proof_seeing(seen, interrupted))
        with pytest.raises(KeyboardInterrupt) as caught:
            split("apply")
        assert f"{PACKAGE}/test_first.py" in seen[0]
        assert caught.value.__notes__ == [f"tools.split: the apply was undone: {UNDONE}"]
        assert everything(split) == before

    @pytest.mark.parametrize("signum", SIGNALS.values(), ids=SIGNALS)
    @pytest.mark.parametrize("where", ["in the proof", "writing the record's id map"])
    def test_a_signal_undoes_the_apply(self, split, monkeypatch, signum, where):
        """SIGTERM or SIGHUP to the process inside the unit: main raises it as Signalled, so
        the apply is undone as Ctrl-C's is, the tree and both id maps byte-identical (an
        earlier apply's ``--idmap`` put back), and the handler there before is there again
        (the coordinator's ruling on 3B-2's signal fork, (a) and (b)).  The proof sees no id
        map written yet: they are the unit's last writes."""
        assert split("record") == 0
        split.idmap.write_bytes(b'{"an": "earlier apply\'s map"}\n')
        before, seen = everything(split), []

        def signalled():
            seen.append((tree(split.root), everything(split)[1:]))
            os.kill(os.getpid(), signum)
            return []
        if where == "in the proof":
            monkeypatch.setattr(cli, "prove_static", lambda *_: signalled())
        else:
            real = Path.write_text

            def write_text(path, *args, **kwargs):
                if path == split.into / "idmap.json":
                    signalled()
                return real(path, *args, **kwargs)
            monkeypatch.setattr(Path, "write_text", write_text)
        kept = []

        def keep(received, _frame):
            kept.append(received)
        previous = signal.signal(signum, keep)
        try:
            with pytest.raises(cli.Signalled) as caught:
                split("apply")
            restored = signal.getsignal(signum)
        finally:
            signal.signal(signum, previous)
        assert (caught.value.signum, kept, restored) == (signum, [], keep)
        assert f"{PACKAGE}/test_first.py" in seen[0][0] and SOURCE not in seen[0][0]
        if where == "in the proof":
            assert seen[0][1] == before[1:]
        assert caught.value.__notes__ == [f"tools.split: the apply was undone: {UNDONE}"]
        assert everything(split) == before

    def test_an_id_map_with_no_directory_is_refused_before_any_write(self, split, capsys,
                                                                    tmp_path):
        """``--idmap`` names a directory that does not exist: refused before anything is
        written (delta review 2's P1: the package was written and the source deleted before
        the map failed)."""
        assert split("record") == 0
        split.idmap = tmp_path / "missing" / "test_sample.idmap.json"
        before = everything(split)
        capsys.readouterr()
        assert split("apply") == 1
        assert (f"REFUSED: {split.idmap}: no directory {split.idmap.parent} to write it in; "
                "nothing is applied") in capsys.readouterr().err
        assert everything(split) == before
        assert not split.idmap.parent.exists()


def test_main_puts_back_the_signal_handlers_it_found(split):
    """main raises SIGTERM and SIGHUP as Signalled only while its command runs: after it, the
    handlers there before are there again, so a caller in the same process keeps its own (the
    coordinator's ruling on 3B-2's signal fork, (a))."""
    def mine(_received, _frame):
        """A handler of the caller's own."""
    previous = {signum: signal.signal(signum, mine) for signum in SIGNALS.values()}
    try:
        assert split("record") == 0
        assert [signal.getsignal(signum) for signum in SIGNALS.values()] == [mine, mine]
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
