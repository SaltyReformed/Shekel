"""The pytest plugin a recorded run loads (``-p tools.testrecord.plugin``): per test, the
lines and branches each of its phases executed in the measured directories, the values every
PASSING ``assert`` compared, and how it ended; each process writes its own file into the
directory ``$SHEKEL_TESTRECORD`` names.  :func:`tools.testrecord.record.record` starts the
run and reads them.

**Lines and branches: coverage.py's own API, one dynamic context per test phase**
(``<node id>|setup``, ``|call``, ``|teardown``), switched by wrappers around pytest's three
phase hooks; outside a phase the context is empty, so imports and collection belong to no
test.  The core is pinned to coverage's C tracer and REFUSED otherwise (:func:`_started`):
on CPython 3.14 coverage's default core is ``sys.monitoring``, which does not support
dynamic contexts (coverage warns ``no-sysmon-context``), and in a two-test probe it
recorded the second test under no context at all (measured 2026-10-16, coverage 7.16.2,
CPython 3.14.8).  ``COVERAGE_CORE`` in the environment overrides a configuration file, so
the core is set on the object after it read its configuration, and read back after it
started.  Branch measurement records ARCS, every executed transition between two lines, a
negative number standing for a code object's entry or exit at the line it starts on
(coverage's own encoding); the lines a phase executed are their positive endpoints.
pytest-cov is not used: its ``--cov-context=test`` would ride the same default core, and
it writes its data where its own configuration says, not into the run's directory.

**Asserts: pytest's public ``pytest_assertion_pass`` hook, its own arguments only**: the
assert's line, its source text and pytest's explanation of it, in the order they passed,
per phase.  pytest calls it only when ``enable_assertion_pass_hook`` is set, and only from
an assert it rewrote: one in a module matching ``python_files``, a ``conftest.py``, a module
named on the command line, or a module registered for rewriting; an assert anywhere else
passes unrecorded.  The
explanation shows each value through pytest's ``saferepr``, which shortens a long one
unless the assertion verbosity is 2 or more (``verbosity_assertions``, which the runner
sets).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import coverage
import pytest

from tools.testrecord.record import (
    CORE,
    MEASURED,
    ORDER_ENV,
    PHASES,
    RECORD_ENV,
    ROOT_ENV,
    WORKER,
    outcomes_path,
    process_name,
    trace_path,
    verdict,
)


def _started(root: Path, directory: Path, process: str) -> coverage.Coverage:
    """A started coverage measurement of :data:`MEASURED` under ``root``, writing into
    ``directory``; refused unless it runs on :data:`CORE`."""
    cov = coverage.Coverage(data_file=str(directory / f"{process}.coverage"), branch=True,
                            source=[str(root / name) for name in MEASURED],
                            config_file=False)
    cov.set_option("run:core", "ctrace")
    cov.start()
    core = dict(cov.sys_info()).get("core")
    if core != CORE:
        cov.stop()
        raise pytest.UsageError(f"coverage measures with {core!r}, not {CORE!r}: "
                                "per-test contexts would be lost")
    return cov


class Tracer:
    """One test-running process's measurement: coverage switched per phase, every passing
    assert per phase, the tests it ran in the order it ran them, the tests it collected,
    and for each test whose call ran the file its function is in (``item.location``), of
    which that call must have measured a line; written at session end."""

    def __init__(self, root: Path, directory: Path, process: str) -> None:
        """Measure under ``root`` into ``directory`` as ``process``."""
        self.root = root
        self.path = trace_path(directory, process)
        self.cov = _started(root, directory, process)
        self.phase: tuple[str, str] | None = None
        self.asserts: dict[str, dict[str, list[list]]] = {}
        self.ran: list[str] = []
        self.called: dict[str, str] = {}

    def _phase(self, item: pytest.Item, when: str):
        """Measure ``item``'s phase ``when`` under its own context."""
        self.phase = (item.nodeid, when)
        self.cov.switch_context(f"{item.nodeid}|{when}")
        try:
            return (yield)
        finally:
            self.cov.switch_context("")
            self.phase = None

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        """The setup phase, under its context."""
        return (yield from self._phase(item, PHASES[0]))

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item):
        """The call phase, under its context, the file its function is in noted."""
        self.called[item.nodeid] = item.location[0]
        return (yield from self._phase(item, PHASES[1]))

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item):
        """The teardown phase, under its context."""
        return (yield from self._phase(item, PHASES[2]))

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> None:
        """Record each test this process RUNS (only that process calls this hook)."""
        self.ran.append(item.nodeid)

    @pytest.hookimpl
    def pytest_assertion_pass(self, item: pytest.Item, lineno: int, orig: str,
                              expl: str) -> None:
        """Record one passing assert under the phase it passed in: its line, its text and
        pytest's explanation."""
        when = self.phase[1] if self.phase and self.phase[0] == item.nodeid else "outside"
        self.asserts.setdefault(item.nodeid, {}).setdefault(when, []).append(
            [lineno, orig, expl])

    def traces(self) -> dict[str, dict[str, dict[str, list[list[int]]]]]:
        """Every measured phase's arcs, by node id, phase and file relative to the root."""
        data = self.cov.get_data()
        found: dict[str, dict[str, dict[str, list[list[int]]]]] = {}
        for filename in sorted(data.measured_files()):
            path = str(Path(filename).relative_to(self.root))
            contexts = {c for held in data.contexts_by_lineno(filename).values() for c in held}
            for context in sorted(contexts - {""}):
                data.set_query_context(context)
                nodeid, when = context.rsplit("|", 1)
                found.setdefault(nodeid, {}).setdefault(when, {})[path] = sorted(
                    [a, b] for a, b in data.arcs(filename) or ())
            data.set_query_contexts(None)
        return found

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        """Stop measuring and write what this process collected, ran, traced and
        asserted (an xdist controller collects nothing; each worker collects every
        test)."""
        self.cov.stop()
        self.cov.save()
        self.path.write_text(json.dumps({
            "ran": self.ran, "collected": [item.nodeid for item in session.items],
            "called": self.called, "traces": self.traces(), "asserts": self.asserts,
        }), encoding="utf-8")


class Outcomes:
    """How every test ended, and every collector that failed, as the process that sees
    every report saw it (an xdist controller, or the lone process); written at session
    end."""

    def __init__(self, directory: Path) -> None:
        """Write into ``directory``."""
        self.directory = directory
        self.phases: dict[str, dict[str, tuple[str, bool]]] = {}
        self.collect_errors: list[str] = []

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Record one phase's outcome, and whether it was an expected failure."""
        self.phases.setdefault(report.nodeid, {})[report.when] = (
            report.outcome, hasattr(report, "wasxfail"))

    @pytest.hookimpl
    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """Record a collector that failed (an xdist controller hears of exactly those)."""
        if report.failed:
            self.collect_errors.append(report.nodeid)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, exitstatus: int) -> None:
        """Write the outcomes, the collectors that failed and the session's exit status."""
        outcomes_path(self.directory).write_text(json.dumps({
            "outcomes": {nodeid: verdict(p) for nodeid, p in sorted(self.phases.items())},
            "collect_errors": self.collect_errors, "exitstatus": int(exitstatus),
        }), encoding="utf-8")


def ordered(items: list[pytest.Item], order: list[str]) -> list[pytest.Item]:
    """``items`` in ``order``; refused unless the two name the same tests once each."""
    by_id = {item.nodeid: item for item in items}
    repeated = len(items) - len(by_id) + len(order) - len(set(order))
    if repeated or set(by_id) != set(order):
        raise pytest.UsageError(
            f"the run order names {len(set(order) - set(by_id))} test(s) not collected, "
            f"omits {len(set(by_id) - set(order))} collected and repeats {repeated} (in "
            "it or among the collected); it must name each collected test exactly once")
    return [by_id[nodeid] for nodeid in order]


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """When ``$SHEKEL_TESTRECORD`` is set, start measuring before any conftest is imported
    and register this process's recorders: a :class:`Tracer` on every process, and an
    :class:`Outcomes` on the one that sees every report.  Refused when pytest's rootdir,
    which names the tests, is not the run's root, under which the files are measured."""
    target = os.environ.get(RECORD_ENV)
    if not target:
        return
    root = Path(os.environ[ROOT_ENV])
    if early_config.rootpath.resolve() != root:
        raise pytest.UsageError(f"pytest's rootdir {early_config.rootpath} is not the run's "
                                f"root {root}: its node ids would not name the files measured")
    directory, process = Path(target), process_name()
    manager = early_config.pluginmanager
    manager.register(Tracer(root, directory, process), "testrecord-tracer")
    if WORKER not in os.environ:
        manager.register(Outcomes(directory), "testrecord-outcomes")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """When ``$SHEKEL_TESTRECORD_ORDER`` names a file, run exactly its tests in its order:
    trylast, so after every implementation that is not itself trylast, the marker and
    keyword deselection among them.  A trylast one registered after this plugin runs after
    it, and :func:`tools.testrecord.record.record` refuses a run it reordered."""
    order = os.environ.get(ORDER_ENV)
    if os.environ.get(RECORD_ENV) and order:
        items[:] = ordered(items, json.loads(Path(order).read_text(encoding="utf-8")))
