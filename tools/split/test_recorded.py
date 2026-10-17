"""The recorded arm (ruling ``balance:R-BAL303``; the coordinator's ruling on PR A's item (4),
2026-10-16): ``record`` runs the source twice in one process recorded test by test, its
verdict refusing a source the instrument sees noise in; ``prove`` runs the split twice in the
source's order and compares what each test did, within each layout and between the layouts,
through the node-id map the plan produces and the written lines' verified origins.  Stubbed
through the ``split`` fixture (:class:`tools.split.conftest.Recording`), and run for real
through a stand-in wrapper on the sample, a known answer the static screen accepts among the
cases."""
from __future__ import annotations

import json
import sys
from zoneinfo import ZoneInfo

import pytest

from tools.ci.arcs import REPO, WRAPPER
from tools.split import _dynamic as dynamic
from tools.split import _run as run
from tools.split._render import Origin, line_limit, rendered
from tools.split.conftest import (
    BEFORE_IDS,
    PACKAGE,
    RAISED,
    SAMPLE,
    SAMPLE_ZONE,
    SOURCE,
    accepted,
    applied,
    committed,
    held,
    layout_of,
    plan_of,
    tree,
)
from tools.split.split import _META, Context, _normalized
from tools.testrecord import record as recorder
from tools.testrecord.record import Record, RecordError, Trace

#: The recorded line of the sample's proof block: nine tests, four passing asserts a run (one
#: in a setup), two phases a run ended by an exception (test_c's skip, test_d's failing
#: assert), and the explanations each kind rewrites (three holding an address and one the
#: root in every run, and three naming a moved module, a part's, ``_helpers`` and
#: ``conftest``, in each of the split's two); neither exception's message holds what a kind
#: rewrites.
RECORDED_LINE = (
    "- recorded: each layout run twice in one process (-n 0, PYTHONHASHSEED=0), the split in "
    "the source's order: 9 tests alike within each layout and between the layouts (the first "
    "run of each) in outcome, in the arcs each phase ran in app/ and tests/ (their lines with "
    "them), in the passing asserts (4 per run) and in the exception that ended each phase "
    "that raised one, by its type's qualified name and its message (2 per run); explanations "
    "and exception messages each normalization kind rewrote over the four runs: tree-root 4, "
    "address 12, csrf-token 0, session-cookie 0, run-socket 0, uuid 0, relocated-repr 6, "
    "set-order 0")

#: test_one's node id before the split, and the line its passing assert's explanation opens.
TEST_ONE, ONE_EQUALS = f"{SOURCE}::TestFirst::test_one", "1 == 1\n"

#: test_d's node id before the split: its call fails, as an expected failure.
TEST_D = f"{SOURCE}::TestBig::test_d"


def _edit(nodeid: str, step):
    """A ``Recording.changed`` entry running ``step(trace, outcomes, nodeid)``, ``nodeid`` read
    in the run's own layout (the split's id when the split runs)."""
    def change(trace: dict, outcomes: dict) -> None:
        own = next(i for i in trace["ran"] if i.split("::", 1)[1] == nodeid.split("::", 1)[1])
        step(trace, outcomes, own)
    return change


def _explained(trace: dict, _outcomes: dict, nodeid: str) -> None:
    """test_one's passing assert explains another value."""
    trace["asserts"][nodeid]["call"][0][2] = "2 == 2\n"


def _failed(_trace: dict, outcomes: dict, nodeid: str) -> None:
    """test_one fails."""
    outcomes["outcomes"][nodeid] = "failed"


def _ran_more(trace: dict, _outcomes: dict, nodeid: str) -> None:
    """test_one's call runs another line of ``tests/shared.py``."""
    trace["traces"][nodeid]["call"]["tests/shared.py"].append([8, 9])


def _composed(trace: dict, _outcomes: dict, nodeid: str) -> None:
    """test_one's call runs its part's first line, the docstring the writer composed."""
    trace["traces"][nodeid]["call"][nodeid.split("::")[0]].append([1, 2])


def _dropped(trace: dict, outcomes: dict, nodeid: str) -> None:
    """The run never collected test_one."""
    for held_ids in (trace["ran"], trace["collected"]):
        held_ids.remove(nodeid)
    for kept in (trace["called"], trace["traces"], trace["asserts"], trace["raised"],
                 outcomes["outcomes"]):
        kept.pop(nodeid, None)


def _tore_down_failing(trace: dict, _outcomes: dict, nodeid: str) -> None:
    """test_d's teardown raises too, after its call failed."""
    trace["raised"][nodeid]["teardown"] = ["RuntimeError", "teardown fails"]


def _reversed(trace: dict, _outcomes: dict) -> None:
    """The run ran its tests in the reverse order (a ``Recording.changed`` entry itself)."""
    trace["ran"].reverse()


def test_a_proved_split_prints_the_recorded_line(split, capsys):
    """Every arm passed: the block's recorded line names how each layout ran (formatted from
    the recorder's constants), the tests, passing asserts and raised exceptions compared, and
    every kind's count over the four runs, zeros included.  The split's explanations name its
    moved class's module and its call runs the written copies of the source's lines, so the
    two layouts agree only through the plan's relocated modules and the verified origins;
    test_one's line of ``tests/shared.py`` agrees by reading as itself."""
    applied(split)
    capsys.readouterr()
    assert split("prove") == 0
    assert RECORDED_LINE in capsys.readouterr().out.splitlines()


def test_the_recorded_line_counts_the_phases_an_exception_ended(split, capsys):
    """test_d's call and teardown each ended by an exception, alike in all four runs: three
    phases a run, in two tests, and the line counts the phases (the branch's neutral review
    of H-1, L3)."""
    for run_name in ("source 1", "source 2", "split 1", "split 2"):
        split.recording.changed[run_name] = _edit(TEST_D, _tore_down_failing)
    applied(split)
    capsys.readouterr()
    assert split("prove") == 0
    assert RECORDED_LINE.count("(2 per run)") == 1
    assert RECORDED_LINE.replace("(2 per run)", "(3 per run)") in (
        capsys.readouterr().out.splitlines())


def test_the_runs_are_taken_in_one_process_the_split_in_the_sources_order(split):
    """The source runs natively in one process; each run of the split is given the order the
    source's first run ran in, read through the plan's map: not the split's own collection
    order, which runs its parts in the order of their file names."""
    applied(split)
    assert split("prove") == 0
    order = [split.stubs.mapping[old] for old in BEFORE_IDS]
    assert split.recording.given == {"source 1": (None, ("-n", "0")),
                                 "source 2": (None, ("-n", "0")),
                                 "split 1": (order, ()), "split 2": (order, ())}
    assert order != sorted(order)


@pytest.mark.parametrize("change,said", [
    (_edit(TEST_ONE, _explained),
     f"recorded: within the source's layout: {TEST_ONE}: asserts in call: the first only"),
    (_reversed, "the source's two recorded runs ran its tests in two orders"),
], ids=["another-value", "another-order"])
def test_a_source_the_instrument_sees_noise_in_is_refused_at_record_and_apply(
        split, capsys, change, said):
    """The source's second run differs from its first: in a passing assert's value, or in the
    order it ran its tests in.  ``record`` fails it and ``apply`` refuses it with nothing
    written: the split is never applied over a source the proof cannot see past."""
    split.recording.changed["source 2"] = change
    assert split("record") == 1
    assert f"FAILED: {said}" in capsys.readouterr().err
    before = tree(split.root)
    assert split("apply") == 1
    assert "REFUSED: the record did not pass" in capsys.readouterr().err
    assert tree(split.root) == before
    assert not split.idmap.exists()


def test_a_source_run_holding_other_tests_than_its_collection_is_refused(split, capsys):
    """Both of the source's runs left test_one out, alike: refused, because the node-id map,
    which orders and names the split's runs, is made from the collection."""
    for number in (1, 2):
        split.recording.changed[f"source {number}"] = _edit(TEST_ONE, _dropped)
    assert split("record") == 1
    err = capsys.readouterr().err
    for number in (1, 2):
        assert (f"FAILED: the source's recorded run {number} ran other tests than its "
                f"collection: 1 test(s) in one only, e.g. ['{TEST_ONE}']") in err


def test_a_split_the_instrument_sees_noise_in_is_refused_at_prove(split, capsys):
    """The split's second run differs from its first: prove fails it, and prints no block."""
    applied(split)
    split.recording.changed["split 2"] = _edit(TEST_ONE, _explained)
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert (f"FAILED: recorded: within the split's layout: "
            f"{split.stubs.mapping[TEST_ONE]}: asserts in call: the first only") in err


@pytest.mark.parametrize("step,said", [
    (_failed, f"{TEST_ONE}: outcome: the first only ('passed',); the second only ('failed',)"),
    (_ran_more, f"{TEST_ONE}: lines in call: the first only (); the second only "
                "(('tests/shared.py', 8), ('tests/shared.py', 9))"),
    (_explained, f"{TEST_ONE}: asserts in call: the first only (('a passing assert', "),
    (_composed, f"{TEST_ONE}: untranslated in call: the first only (); the second only "
                f"(('{PACKAGE}/test_first.py', 1), ('{PACKAGE}/test_first.py', 2))"),
], ids=["outcome", "lines", "asserts", "a-composed-line"])
def test_a_split_that_changes_what_a_test_does_is_refused(split, capsys, step, said):
    """Both of the split's runs alike, and unlike the source's: another outcome, a line run
    in a file the split left alone, another value compared, or a line the writer composed
    run.  Each is a difference between the layouts, named by the source's node id."""
    applied(split)
    for number in (1, 2):
        split.recording.changed[f"split {number}"] = _edit(TEST_ONE, step)
    capsys.readouterr()
    assert split("prove") == 1
    assert (f"FAILED: recorded: between the layouts (the first run of each): {said}"
            in capsys.readouterr().err)


def test_an_import_line_the_split_filtered_stands_for_no_source_line(split, capsys):
    """Both of the split's runs run test_first.py's import of ``helper_one``, a line the writer
    filtered from a source import statement and so no copy of a source line: it stands for
    none, an untranslated difference, though its origin names the source statement."""
    part = f"{PACKAGE}/test_first.py"
    imported = next(number for number, (_, origin) in enumerate(
        rendered(layout_of(split.root), line_limit(split.root))["test_first.py"], start=1)
                    if isinstance(origin, Origin) and not origin.copied)
    applied(split)

    def ran_import(trace: dict, _outcomes: dict, nodeid: str) -> None:
        trace["traces"][nodeid]["call"][part].append([-imported, imported])
    for number in (1, 2):
        split.recording.changed[f"split {number}"] = _edit(TEST_ONE, ran_import)
    capsys.readouterr()
    assert split("prove") == 1
    assert (f"{TEST_ONE}: untranslated in call: the first only (); the second only "
            f"(('{part}', {imported}),)") in capsys.readouterr().err


def test_a_split_run_that_does_not_record_leaves_every_other_reason_printed(split, capsys):
    """The split's first recorded run does not record (here its reader refuses it, as it
    refuses an interrupted session): that is the recorded arm's failure, and the other arms'
    failures (the plan gate's, here) are still printed beside it, never hidden behind one
    refusal (the branch's neutral review of item (4), M1)."""
    applied(split)
    split.stubs.gate = (1, "1 failed")

    def refused(_trace: dict, _outcomes: dict) -> None:
        raise RecordError("the session did not run to its end (exit status 4)")
    split.recording.changed["split 1"] = refused
    capsys.readouterr()
    assert split("prove") == 1
    err = capsys.readouterr().err
    assert ("FAILED: recorded: the split's recorded run 1 did not record: the session did not "
            "run to its end (exit status 4)\n") in err
    assert "FAILED: plan gate: 1 failed\n" in err


@pytest.mark.parametrize("command,run_name", [("record", "source 1"), ("prove", "split 1")])
def test_a_tree_edited_while_a_recorded_run_runs_is_refused(split, capsys, command, run_name):
    """A tracked file edited while the source's first recorded run runs, or the split's: the
    command refuses, because its recorded runs run before its last judgement of the tree
    (the coordinator's ruling on item (4)'s Fork 1 (i)); a record writes no token."""
    if command == "prove":
        applied(split)
    shared = split.root / "tests/shared.py"

    def edited(_trace: dict, _outcomes: dict) -> None:
        shared.write_text(shared.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    split.recording.changed[run_name] = edited
    capsys.readouterr()
    assert split(command) == 1
    assert ("the working tree changed at 1 path(s), e.g. ['tests/shared.py'] while the runs "
            "ran") in capsys.readouterr().err
    assert (split.into / _META).exists() == (command == "prove")


def test_a_long_side_is_shown_cut_at_the_excerpt():
    """A difference whose sides are long is shown with each side's repr cut at
    :data:`tools.split._dynamic.EXCERPT` characters and marked as cut."""
    def one(explanation: str) -> Record:
        return Record(tests={"t": Trace(outcome="passed", arcs={}, asserts={"call": (
            (1, "x", explanation),)}, raised={})}, order=("t",), exitstatus=0)
    line, = dynamic.recorded_failures("here", one("a" * 1000), one("b" * 1000))
    first = repr((("x", "a" * 1000),))[:dynamic.EXCERPT]
    second = repr((("x", "b" * 1000),))[:dynamic.EXCERPT]
    assert line == (f"recorded: here: t: asserts in call: the first only {first}...; "
                    f"the second only {second}...")


def test_every_arm_reads_the_map_the_plan_produces(split, capsys):
    """An ``--idmap`` file edited by hand after apply (one test mapped to an id no run has) is
    that arm's failure alone, never a traceback: the collection, outcome and recorded arms
    read the map the plan produces (the coordinator's ruling on item (4)'s Fork 3)."""
    applied(split)
    edited = json.loads(split.idmap.read_text(encoding="utf-8"))
    edited[TEST_ONE] = f"{PACKAGE}/test_first.py::TestFirst::test_none"
    split.idmap.write_text(json.dumps(edited), encoding="utf-8")
    capsys.readouterr()
    assert split("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert err == f"FAILED: {split.idmap} is not the map this plan produces\n"


def test_a_source_record_edited_after_apply_leaves_the_split_unrecorded(split, capsys):
    """The source's first recorded run, rewritten after apply to have run one test fewer (as
    a record edited by hand would be): prove fails the record again and says the split was
    not recorded, with no order to give it, rather than raising."""
    applied(split)
    run_dir = held(split, "before_recorded_1")
    trace = json.loads(recorder.trace_path(run_dir, recorder.LONE).read_text(encoding="utf-8"))
    outcomes = json.loads(recorder.outcomes_path(run_dir).read_text(encoding="utf-8"))
    _dropped(trace, outcomes, TEST_ONE)
    recorder.trace_path(run_dir, recorder.LONE).write_text(json.dumps(trace), encoding="utf-8")
    recorder.outcomes_path(run_dir).write_text(json.dumps(outcomes), encoding="utf-8")
    capsys.readouterr()
    assert split("prove") == 1
    err = capsys.readouterr().err
    assert "FAILED: record: the source's recorded run 1 ran other tests than its collection" in err
    assert ("FAILED: recorded: the split was not recorded: the source's first recorded run did "
            "not run exactly the tests the plan's node-id map names") in err


def test_a_record_is_normalized_under_the_root_it_prints(tmp_path):
    """A recorded run prints the RESOLVED root, even when the tool is handed the root through
    a symlink (measured 2026-10-16): so the tree-root kind is handed the resolved root, and
    rewrites the root a record holds."""
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    held_record = Record(tests={"t": Trace(outcome="passed", arcs={}, asserts={"call": (
        (1, "x", f"PosixPath('{real}/tests') != None"),)}, raised={})}, order=("t",),
                         exitstatus=0)
    ctx = Context(plan=plan_of(), root=tmp_path / "link", into=tmp_path / "into",
                  zone=ZoneInfo(SAMPLE_ZONE))
    found, kinds = _normalized(ctx, held_record)
    assert kinds["tree-root"] == 1
    assert found.tests["t"].asserts["call"][0][2] == "PosixPath('<root>/tests') != None"


#: The sample's wrapper for the real runs: this interpreter's pytest, run as the wrapper is.
STAND_IN = f'#!/bin/sh\nexec "{sys.executable}" -m pytest "$@"\n'

#: The real door and recorder, kept before any fixture stubs them.
_REAL_OBSERVE, _REAL_RECORD = run.observe, recorder.record


@pytest.fixture(name="real")
def _real(split, monkeypatch):
    """The ``split`` fixture with the suite runs and the recorded runs REAL, through a
    stand-in wrapper committed in the sample (pytest itself; the tool suite starts no
    docker), this repository on the path for the plugins; pylint and the plan gate stay
    stubbed.  A ``.gitignore`` holds the bytecode pytest writes, as the repository's does."""
    monkeypatch.setattr(run, "observe", _REAL_OBSERVE)
    monkeypatch.setattr(recorder, "record", _REAL_RECORD)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    wrapper = split.root / WRAPPER
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(STAND_IN, encoding="utf-8")
    wrapper.chmod(0o755)
    (split.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    committed(split.root, "the stand-in wrapper")
    return split


def test_the_sample_split_is_proved_by_its_real_runs(real, capsys):
    """Record, apply and prove through real pytest runs of the sample: the block is printed,
    its recorded line counting what the real runs recorded.  The exceptions a real run of
    the source records are the stub's (:data:`tools.split.conftest.RAISED`), so the stubbed
    cases measure what a real run holds (the branch's neutral review of H-1, L3)."""
    applied(real)
    capsys.readouterr()
    assert real("prove") == 0
    out = capsys.readouterr().out
    assert "- recorded: each layout run twice in one process (-n 0, PYTHONHASHSEED=0), the " \
           "split in the source's order: 9 tests alike" in out
    source = recorder.read(held(real, "before_recorded_1"))
    assert {nodeid: {phase: list(entry) for phase, entry in trace.raised.items()}
            for nodeid, trace in source.tests.items() if trace.raised} == RAISED


#: test_top_level, asserting on its helper pickled: pickle writes a function by reference,
#: its module and name, so the bytes name the module the helper is defined in.
PICKLED = SAMPLE.replace("import re\n", "import pickle\nimport re\n", 1).replace(
    '    """A module-level test."""\n    assert _START.year == 2026\n',
    '    """A module-level test."""\n    assert _START.year == 2026\n'
    '    assert pickle.dumps(_make) != b""\n', 1)


def test_a_moved_helper_read_by_its_module_is_flagged_though_the_screen_accepts_it(real,
                                                                                     capsys):
    """A known answer the static screen ACCEPTS (measured at 0afc9bc28): test_top_level passes
    in both layouts, and its assert compares its helper pickled, which names the module the
    helper is defined in: the source before, ``_helpers`` after.  The outcome arms see
    nothing; the recorded arm flags the compared value."""
    (real.root / SOURCE).write_text(PICKLED, encoding="utf-8")
    committed(real.root, "the pickled helper")
    applied(real)
    capsys.readouterr()
    assert real("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    assert err.startswith(f"FAILED: recorded: between the layouts (the first run of each): "
                          f"{SOURCE}::test_top_level: asserts in call: ")
    line, = err.splitlines()
    assert r"tests.test_area.test_sample\\x94\\x8c\\x05_make" in line
    assert r"tests.test_area.test_sample._helpers\\x94\\x8c\\x05_make" in line


#: The same known answer FAILING in both layouts (delta review 4's H-1 probe): the assert
#: compares the pickled helper with nothing, so no passing assert records the value, and only
#: the exception that ended the call, pytest's explanation, holds the module it names.
FAILING_PICKLE = PICKLED.replace('    assert pickle.dumps(_make) != b""\n',
                                 '    assert pickle.dumps(_make) == b""\n', 1)


def test_a_moved_helper_read_by_its_module_in_a_failing_assert_is_flagged(real, capsys):
    """test_top_level FAILS in both layouts, alike in outcome, arcs and the assert that passed
    before it failed; its failing comparison holds the module the helper is defined in, the
    source before and ``_helpers`` after.  The recorded arm flags the exception that ended the
    call (the coordinator's ruling on delta review 4's H-1, 2026-10-16: before it, prove
    printed its block over this split)."""
    assert FAILING_PICKLE != PICKLED
    (real.root / SOURCE).write_text(FAILING_PICKLE, encoding="utf-8")
    committed(real.root, "the pickled helper, failing")
    applied(real)
    capsys.readouterr()
    assert real("prove") == 1
    out, err = capsys.readouterr()
    assert out == "prove: FAILED\n"
    line, = err.splitlines()
    assert line.startswith(f"FAILED: recorded: between the layouts (the first run of each): "
                           f"{SOURCE}::test_top_level: raised in call: the first only "
                           "('AssertionError', ")
    assert r"tests.test_area.test_sample\\x94\\x8c\\x05_make" in line
    assert r"tests.test_area.test_sample._helpers\\x94\\x8c\\x05_make" in line


def test_the_screen_accepts_the_pickled_helper(tmp_path):
    """The known answer's premise, held: the static screen lays the pickled helper's source
    out with no refusal, so only running it can see what the split changes."""
    accepted(tmp_path, PICKLED)
