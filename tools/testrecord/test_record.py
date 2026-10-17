"""A recorded run through a stand-in wrapper (:func:`tools.testrecord.record.record`), what it
reads back (:func:`tools.testrecord.record.read`), and how a test ended
(:func:`tools.testrecord.record.verdict`).

The wrapper run's stopping under an exception is graded through the door that uses it,
:func:`tools.split._run.observe` (``tools/split/test_run.py``), against a stand-in for
``scripts/test.sh``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools.ci.arcs import WRAPPER
from tools.testrecord.conftest import SAMPLE_IDS
from tools.testrecord.record import (
    HASH_SEED,
    ORDER_ENV,
    RecordError,
    outcomes_path,
    read,
    record,
    trace_path,
    verdict,
)

#: Each sample test's outcome.
OUTCOMES = dict(zip(SAMPLE_IDS, ("passed", "passed", "passed", "failed", "skipped",
                                 "xfailed", "xpassed", "error")))

#: The sample's app module, as the record names it.
CALC = "app/calc.py"

#: A sample conftest whose hook runs after each phase has ended, a passing assert in it.
BETWEEN = textwrap.dedent('''\
    def pytest_runtest_logreport(report):
        assert report.when in ("setup", "call", "teardown")
    ''')

#: A sample test module asserting the run's environment names no xdist session and no
#: order; and its one test's id.
UNINHERITED = textwrap.dedent('''\
    import os


    def test_no_inherited_session():
        assert not {"PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
                    "PYTEST_XDIST_TESTRUNUID", "SHEKEL_TESTRECORD_ORDER"} & set(os.environ)
    ''')
UNINHERITED_ID = "tests/test_environment.py::test_no_inherited_session"

#: A sample test module asserting the run's hash seed and its effect, a string's hash (both
#: filled in by the case); and its one test's id.
SEEDED = textwrap.dedent('''\
    import os


    def test_the_hash_seed_is_the_runs():
        assert os.environ["PYTHONHASHSEED"] == {seed!r}
        assert hash("shekel") == {hashed}
    ''')
SEEDED_ID = "tests/test_seed.py::test_the_hash_seed_is_the_runs"

#: A sample test module raising a nested class's exception from another; and its one test's
#: id.
CHAINED = textwrap.dedent('''\
    class Outer:
        class Error(Exception):
            pass


    def test_chained():
        try:
            raise KeyError("the cause")
        except KeyError as exc:
            raise Outer.Error("the error") from exc
    ''')
CHAINED_ID = "tests/test_chained.py::test_chained"

#: A sample conftest reordering the tests after every hook that is not itself trylast.
REVERSING = textwrap.dedent('''\
    import pytest


    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(items):
        items.reverse()
    ''')

#: A sample test module whose first test displaces the trace function, so nothing after it
#: is measured.
DISPLACING = textwrap.dedent('''\
    import sys


    def test_displaces():
        sys.settrace(None)


    def test_after():
        assert True
    ''')


class TestTheVerdict:
    """Each combination of phase reports names one outcome."""

    @pytest.mark.parametrize("phases, status", [
        ({"setup": ("passed", False), "call": ("passed", False)}, "passed"),
        ({"setup": ("passed", False), "call": ("failed", False)}, "failed"),
        ({"setup": ("failed", False)}, "error"),
        ({"setup": ("skipped", False)}, "skipped"),
        ({"setup": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("skipped", True)}, "xfailed"),
        ({"setup": ("passed", False), "call": ("passed", True)}, "xpassed"),
        ({"setup": ("passed", False), "call": ("passed", False),
          "teardown": ("failed", False)}, "passed+teardown-error"),
    ])
    def test_verdict(self, phases, status):
        """Each phase combination names its outcome."""
        assert verdict(phases) == status


class TestARecordedRun:
    """The sample run through the stand-in wrapper, read back."""

    def test_each_test_ends_as_it_did(self, project, out):
        """Every outcome the sample holds, each test once, in the order it ran."""
        found = record(project, ["tests"], out, extra=("-n", "0"))
        assert {nodeid: t.outcome for nodeid, t in found.tests.items()} == OUTCOMES
        assert found.order == tuple(SAMPLE_IDS)
        assert found.exitstatus == pytest.ExitCode.TESTS_FAILED

    def test_each_case_holds_the_arcs_it_took(self, project, out):
        """The two parametrized cases took opposite sides of the app's branch, each its own
        arcs and none of the other's (a negative line is the function's entry or exit)."""
        found = record(project, ["tests"], out, extra=("-n", "0"))
        one, three = (found.tests[f"tests/test_sample.py::test_branch[{v}]"] for v in (1, 3))
        assert one.arcs["call"][CALC] == {(-1, 2), (2, 4), (4, -1)}
        assert three.arcs["call"][CALC] == {(-1, 2), (2, 3), (3, -1)}

    def test_each_phase_holds_its_own_arcs_and_asserts(self, project, out):
        """A fixture's setup and teardown are their phases', the test's body its call's,
        and each passing assert is in the phase it passed in, in order."""
        test = record(project, ["tests"], out, extra=("-n", "0")).tests[
            "tests/test_sample.py::test_long"]
        assert set(test.arcs) == {"setup", "call", "teardown"}
        assert [orig for _, orig, _ in test.asserts["call"]] == [
            '"a" * 300 == "a" * 300', "two == 2"]
        assert [orig for _, orig, _ in test.asserts["teardown"]] == ["value == 2"]
        assert "setup" not in test.asserts

    def test_each_phase_that_raised_holds_the_exception_that_ended_it(self, project, out):
        """A failing assert's call holds pytest's explanation, a skip's setup its reason, an
        expected failure's call the assert it failed on and a broken fixture's setup its
        error, each by its type's name; a test that passed, the xpass included, holds none
        (the coordinator's ruling on delta review 4's H-1, 2026-10-16)."""
        found = record(project, ["tests"], out, extra=("-n", "0"))
        failed = ("AssertionError", "assert 2 == 3\n +  where 2 = double(1)")
        assert {nodeid: t.raised for nodeid, t in found.tests.items() if t.raised} == {
            "tests/test_sample.py::test_fails": {"call": failed},
            "tests/test_sample.py::test_skipped": {"setup": ("Skipped", "skipped")},
            "tests/test_sample.py::test_xfail": {"call": failed},
            "tests/test_sample.py::test_error": {"setup": ("RuntimeError", "setup fails")}}

    def test_an_exception_is_kept_by_its_qualified_name_and_its_own_message(self, project,
                                                                             out):
        """A nested class's exception raised from another is kept as ``Outer.Error`` and its
        own message: never by its bare name, and never with its cause's (the branch's neutral
        review of H-1, L3)."""
        (project / "tests/test_chained.py").write_text(CHAINED, encoding="utf-8")
        found = record(project, ["tests/test_chained.py"], out, extra=("-n", "0"))
        assert found.tests[CHAINED_ID].raised == {"call": ("Outer.Error", "the error")}

    def test_a_long_value_is_shown_whole(self, project, out):
        """At assertion verbosity 2 pytest shortens no value: the 300-character string is
        in the explanation whole (under ``-q`` it would be cut to 240)."""
        test = record(project, ["tests"], out, extra=("-n", "0")).tests[
            "tests/test_sample.py::test_long"]
        assert "a" * 300 in test.asserts["call"][0][2]

    def test_a_module_rewritten_before_without_the_hook_is_rewritten_again(self, project,
                                                                            out, monkeypatch):
        """A plain run first writes the test module's rewritten form, made without the
        assert-pass hook, into the project's ``__pycache__``; the recorded run rewrites
        it again under its own prefix, so its asserts are recorded, and removes that
        prefix when it ends."""
        monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
        subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p",
                        "no:cacheprovider"], cwd=project, capture_output=True, check=False,
                       env={**os.environ, "PYTHONPATH": str(project)})
        assert list((project / "tests/__pycache__").glob("test_sample*.pyc"))
        found = record(project, ["tests"], out, extra=("-n", "0"))
        assert found.tests["tests/test_sample.py::test_branch[1]"].asserts["call"]
        assert not (out / "pycache").exists()

    def test_a_run_order_is_the_order_run(self, project, out):
        """An order runs exactly its tests, in its order, in one process."""
        order = list(reversed(SAMPLE_IDS))
        assert record(project, ["tests"], out, order=order).order == tuple(order)

    def test_under_xdist_every_test_is_traced_once(self, project, out):
        """Workers trace the tests they ran, the controller writes how each ended; no
        order, since no one process ran them all."""
        found = record(project, ["tests"], out, extra=("-n", "2"))
        assert {nodeid: t.outcome for nodeid, t in found.tests.items()} == OUTCOMES
        assert found.tests["tests/test_sample.py::test_branch[3]"].arcs["call"][CALC] == {
            (-1, 2), (2, 3), (3, -1)}
        assert not found.order

    def test_the_wrappers_output_is_kept(self, project, out):
        """What the wrapper wrote is in the run's directory, for a refusal to point at."""
        record(project, ["tests"], out, extra=("-n", "0"))
        assert b"test_sample.py" in (out / "wrapper-output.txt").read_bytes()

    def test_what_runs_between_phases_is_in_no_phase(self, project, out):
        """A hook that runs after a phase has ended runs outside every phase: its lines are
        in no phase's arcs, and its passing assert is filed outside, once per phase
        reported."""
        (project / "tests/conftest.py").write_text(BETWEEN, encoding="utf-8")
        test = record(project, ["tests"], out, extra=("-n", "0")).tests[
            "tests/test_sample.py::test_long"]
        assert not [phase for phase, files in test.arcs.items() if "tests/conftest.py" in files]
        assert [orig for _, orig, _ in test.asserts["outside"]] == [
            'report.when in ("setup", "call", "teardown")'] * 3

    def test_a_run_started_inside_a_worker_is_a_run_of_its_own(self, project, out, tmp_path,
                                                                monkeypatch):
        """``record`` called from a test an xdist worker runs: the worker's identity, and an
        order left in the environment, belong to that worker's session and not to the
        run, whose own tests see none of them."""
        stale = tmp_path / "stale-order.json"
        stale.write_text(json.dumps(SAMPLE_IDS[:1]), encoding="utf-8")
        for name, value in (("PYTEST_XDIST_WORKER", "gw7"), ("PYTEST_XDIST_WORKER_COUNT", "8"),
                            ("PYTEST_XDIST_TESTRUNUID", "inherited"), (ORDER_ENV, str(stale))):
            monkeypatch.setenv(name, value)
        (project / "tests/test_environment.py").write_text(UNINHERITED, encoding="utf-8")
        found = record(project, ["tests"], out, extra=("-n", "0"))
        assert {nodeid: t.outcome for nodeid, t in found.tests.items()} == {
            UNINHERITED_ID: "passed", **OUTCOMES}
        assert found.order == (UNINHERITED_ID, *SAMPLE_IDS)

    @pytest.mark.parametrize("processes", ["0", "2"], ids=["lone", "xdist"])
    def test_every_process_hashes_with_the_pinned_seed(self, project, out, monkeypatch,
                                                       processes):
        """A caller holding a seed of its own (``random``) does not pass it on: the run's
        process, and each xdist worker, hashes with :data:`HASH_SEED`, read in its
        environment and in a string's hash, which a fresh interpreter under that seed
        computes."""
        monkeypatch.setenv("PYTHONHASHSEED", "random")
        hashed = subprocess.run([sys.executable, "-c", "print(hash('shekel'))"],
                                env={**os.environ, "PYTHONHASHSEED": HASH_SEED},
                                capture_output=True, text=True, check=True).stdout.strip()
        (project / "tests/test_seed.py").write_text(
            SEEDED.format(seed=HASH_SEED, hashed=hashed), encoding="utf-8")
        found = record(project, ["tests/test_seed.py"], out, extra=("-n", processes))
        assert {nodeid: t.outcome for nodeid, t in found.tests.items()} == {
            SEEDED_ID: "passed"}


class TestARefusedRun:
    """A run that cannot record what it exists to record is refused."""

    def test_a_directory_holding_anything_is_refused(self, project, out):
        """The run's directory must be empty: nothing in it may be older than the run."""
        (out / "left").write_text("", encoding="utf-8")
        with pytest.raises(RecordError, match="not an empty directory"):
            record(project, ["tests"], out)

    def test_an_order_with_its_own_process_count_is_refused(self, project, out):
        """An order runs in one process; a ``-n`` beside it would contradict it."""
        with pytest.raises(RecordError, match="takes no -n"):
            record(project, ["tests"], out, order=SAMPLE_IDS, extra=("-n", "2"))

    def test_an_order_naming_another_set_of_tests_is_refused(self, project, out):
        """An order omitting a collected test stops the run before any test runs."""
        with pytest.raises(RecordError, match="did not run to its end"):
            record(project, ["tests"], out, order=SAMPLE_IDS[1:])

    def test_a_session_that_collected_nothing_is_refused(self, project, out):
        """An empty session records no test, so it records nothing to compare."""
        with pytest.raises(RecordError, match="did not run to its end"):
            record(project, ["tests"], out, extra=("-k", "no_such_test"))

    @pytest.mark.parametrize("arm", [{"extra": ("-n", "0", "-x")},
                                     {"order": SAMPLE_IDS, "extra": ("-x",)}],
                             ids=["lone", "order"])
    def test_a_session_stopped_early_is_refused(self, project, out, arm):
        """``-x`` ends the session at the first failure, and it exits as a session whose
        tests failed: the tests after that one were collected and never ran."""
        with pytest.raises(RecordError, match="did not run every test it collected"):
            record(project, ["tests"], out, **arm)

    def test_a_collection_error_is_refused(self, project, out):
        """A module that cannot be collected holds tests no run records, even when pytest is
        told to go on past it."""
        (project / "tests/test_broken.py").write_text("import no_such_module\n",
                                                      encoding="utf-8")
        with pytest.raises(RecordError, match="failed to collect"):
            record(project, ["tests"], out,
                   extra=("-n", "0", "--continue-on-collection-errors"))

    def test_a_rootdir_other_than_the_root_is_refused(self, project, out, tmp_path):
        """pytest's rootdir names the tests and the run's root the files measured: a run
        whose two differ is stopped before any test runs, not recorded with nothing
        measured."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with pytest.raises(RecordError, match="wrote no outcomes"):
            record(project, ["tests"], out, extra=("-n", "0", "--rootdir", str(elsewhere)))
        assert b"is not the run's root" in (out / "wrapper-output.txt").read_bytes()

    def test_a_test_whose_measurement_was_lost_is_refused(self, project, out):
        """A test that displaces the trace function leaves the tests after it measured by
        nothing: each whose call ran with no line of its own file measured is refused, by
        its id and its file."""
        (project / "tests/test_untraced.py").write_text(DISPLACING, encoding="utf-8")
        with pytest.raises(RecordError, match="tests/test_untraced.py::test_after in "
                                              "tests/test_untraced.py"):
            record(project, ["tests/test_untraced.py"], out, extra=("-n", "0"))

    def test_an_order_another_hook_changes_is_refused(self, project, out):
        """A hook that reorders the tests after the run order was applied runs them in an
        order other than the one asked for: refused, not recorded as that order."""
        (project / "tests/conftest.py").write_text(REVERSING, encoding="utf-8")
        with pytest.raises(RecordError, match="ran its tests in another order"):
            record(project, ["tests"], out, order=SAMPLE_IDS)

    def test_a_run_that_wrote_no_outcomes_is_refused(self, project, out):
        """A wrapper that ends before pytest starts recorded nothing: the refusal names its
        exit status, and its output is kept."""
        (project / WRAPPER).write_text("#!/bin/sh\necho no cluster\nexit 3\n",
                                       encoding="utf-8")
        with pytest.raises(RecordError, match=r"wrote no outcomes \(exit 3\)"):
            record(project, ["tests"], out)
        assert (out / "wrapper-output.txt").read_bytes() == b"no cluster\n"


class TestTheReader:
    """What :func:`tools.testrecord.record.read` refuses in a run's files."""

    @staticmethod
    def written(out: Path, outcomes: dict, processes: dict[str, list[str]],
                collected: dict[str, list[str]] | None = None, errors: tuple = ()) -> None:
        """A run's files: the outcomes and the collectors that failed (``errors``), and each
        process's tests, traced with nothing and collected as ``collected`` names (as run
        when None)."""
        outcomes_path(out).write_text(json.dumps(
            {"outcomes": outcomes, "exitstatus": 0, "collect_errors": list(errors)}),
            encoding="utf-8")
        for process, ran in processes.items():
            trace_path(out, process).write_text(json.dumps(
                {"ran": ran, "collected": (collected or processes)[process], "called": {},
                 "traces": {}, "asserts": {}, "raised": {}}), encoding="utf-8")

    def test_a_collected_test_no_process_ran_is_refused(self, out):
        """A test collected and never run was not recorded: its session stopped early."""
        self.written(out, {"t": "passed"}, {"main": ["t"]}, {"main": ["t", "u"]})
        with pytest.raises(RecordError, match="did not run every test it collected"):
            read(out)

    def test_a_collector_that_failed_is_refused(self, out):
        """The tests a failed collector holds were never collected, so no run of them is."""
        self.written(out, {"t": "passed"}, {"main": ["t"]}, errors=("tests/test_x.py",))
        with pytest.raises(RecordError, match="failed to collect"):
            read(out)

    def test_a_test_two_processes_ran_is_refused(self, out):
        """One test is one run of it."""
        self.written(out, {"t": "passed"}, {"gw0": ["t"], "gw1": ["t"]})
        with pytest.raises(RecordError, match="ran in two processes"):
            read(out)

    def test_an_outcome_no_process_traced_is_refused(self, out):
        """A test some process ran and no process traced was not recorded."""
        self.written(out, {"t": "passed", "u": "passed"}, {"gw0": ["t"]})
        with pytest.raises(RecordError, match="an outcome and no trace"):
            read(out)

    def test_a_file_written_before_the_recorder_kept_exceptions_is_refused(self, out):
        """A process file holding no exceptions was written by a recorder that did not keep
        them: refused by name, never read as a record in which nothing raised (the branch's
        neutral review of H-1, L4)."""
        self.written(out, {"t": "passed"}, {"main": ["t"]})
        held = json.loads(trace_path(out, "main").read_text(encoding="utf-8"))
        del held["raised"]
        trace_path(out, "main").write_text(json.dumps(held), encoding="utf-8")
        with pytest.raises(RecordError, match="holds no exceptions: .*record again"):
            read(out)

    def test_a_trace_with_no_outcome_is_refused(self, out):
        """A test traced and never reported did not end."""
        self.written(out, {"t": "passed"}, {"gw0": ["t", "u"]})
        with pytest.raises(RecordError, match="a trace and no outcome"):
            read(out)
