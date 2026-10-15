"""A pytest plugin the split tool loads into a run (``-p tools.split._observe``) to record,
as JSON at the path ``$SHEKEL_SPLIT_OBSERVE`` names, what the run collected and deselected
and how every test ended; and, beside it, every fixture each process set up.

**Why a plugin and not the JUnit report**: JUnit XML writes a non-strict XPASS as a plain
pass, so a test that started passing unexpectedly would read the same before and after.
This reads each phase's report itself, ``wasxfail`` included, on the process that sees
every report: the xdist controller, or the lone process under ``-n 0``.

**Every fixture SET UP, through pytest's public hook** (the coordinator's ruling on the
follow-up leaf's fork F, 2026-10-15).  A fixture shared beyond one test, or registered on
the source by an import, changes reach when the source becomes a package; which fixtures a
test reaches is pytest's own resolution (autouse, ``usefixtures``, parameters, overrides,
``request.getfixturevalue``), so it is observed, never re-derived.  Fixtures are set up
where the tests run, on each xdist worker or the lone process, so each writes its own file
(:func:`setups_path`) naming every fixture it set up and every test it ran, and ``record``
merges them and holds the tests they ran to the run's own.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

#: The environment variable naming the file the observation is written to.
OBSERVE_ENV = "SHEKEL_SPLIT_OBSERVE"

#: The process name a run without xdist workers writes its fixture setups under.
LONE = "main"


def setups_path(target: Path, process: str) -> Path:
    """Where ``process`` (an xdist worker's id, or :data:`LONE`) writes the fixtures it set
    up, beside the observation at ``target``."""
    return target.with_name(f"{target.name}.setups-{process}.json")


def setup_key(setup: dict) -> tuple:
    """What makes two recorded setups one fixture: its name, its scope, the node it is
    registered on and the file its function is defined in."""
    return setup["argname"], setup["scope"], setup["baseid"], setup["file"]


def defined_in(function: object) -> str | None:
    """The resolved file a fixture's function is defined in, read from its code object; None
    when it has none to read."""
    code = getattr(inspect.unwrap(function), "__code__", None)
    return str(Path(code.co_filename).resolve()) if code is not None else None


def verdict(phases: dict[str, tuple[str, bool]]) -> str:
    """One test's outcome from its phases' ``(outcome, wasxfail)``: ``passed``, ``failed``,
    ``error``, ``skipped``, ``xfailed`` or ``xpassed``, with ``+teardown-error`` appended
    when its teardown failed."""
    setup, call, teardown = (phases.get(w) for w in ("setup", "call", "teardown"))
    if setup and setup[0] == "failed":
        status = "error"
    elif setup and setup[0] == "skipped":
        status = "xfailed" if setup[1] else "skipped"
    elif call is None:
        status = "not-run"
    elif call[0] == "passed":
        status = "xpassed" if call[1] else "passed"
    elif call[0] == "failed":
        status = "failed"
    else:
        status = "xfailed" if call[1] else "skipped"
    if teardown and teardown[0] == "failed":
        status += "+teardown-error"
    return status


class Observer:
    """Accumulates one run's collection and reports, and writes them at session end."""

    def __init__(self, path: Path) -> None:
        """Observe into ``path``, written when the session finishes."""
        self.path = path
        self.collected: list[str] = []
        self.deselected: list[str] = []
        self.phases: dict[str, dict[str, tuple[str, bool]]] = {}

    @pytest.hookimpl
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Record the collected node ids (empty on an xdist controller, which collects
        nothing itself)."""
        self.collected = [item.nodeid for item in session.items]

    @pytest.hookimpl
    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        """Record what a marker expression or a shard deselected."""
        self.deselected.extend(item.nodeid for item in items)

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Record one phase's outcome, and whether it was an expected failure."""
        self.phases.setdefault(report.nodeid, {})[report.when] = (
            report.outcome, hasattr(report, "wasxfail"))

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, exitstatus: int) -> None:
        """Write the observation."""
        self.path.write_text(json.dumps({
            "collected": self.collected,
            "deselected": sorted(self.deselected),
            "outcomes": {nodeid: verdict(p) for nodeid, p in sorted(self.phases.items())},
            "exitstatus": int(exitstatus),
        }, indent=1), encoding="utf-8")


class SetupObserver:
    """Accumulates the fixtures one process set up and the tests it ran, and writes them at
    session end."""

    def __init__(self, path: Path) -> None:
        """Observe into ``path``, written when the session finishes."""
        self.path = path
        self.setups: dict[tuple, dict] = {}
        self.ran: set[str] = set()

    @pytest.hookimpl(tryfirst=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef,
                             request: pytest.FixtureRequest) -> None:
        """Record one fixture as pytest sets it up: its name, its scope, the node it is
        registered on and the file its function is defined in, once each."""
        setup = {"argname": fixturedef.argname, "scope": fixturedef.scope,
                 "baseid": fixturedef.baseid, "file": defined_in(fixturedef.func)}
        self.setups.setdefault(setup_key(setup), {**setup, "by": request.node.nodeid})

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> None:
        """Record each test this process RUNS.  Only the process executing a test calls this
        hook; an xdist controller is handed every worker's reports, so a report hook would
        count a test whose worker's file is missing as observed."""
        self.ran.add(item.nodeid)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self) -> None:
        """Write what this process set up and ran."""
        self.path.write_text(json.dumps({
            "setups": sorted(self.setups.values(), key=lambda s: (s["argname"], s["baseid"])),
            "ran": sorted(self.ran),
        }, indent=1), encoding="utf-8")


def pytest_configure(config: pytest.Config) -> None:
    """When ``$SHEKEL_SPLIT_OBSERVE`` is set, register an :class:`Observer` on the controller
    and a :class:`SetupObserver` on every process that runs tests."""
    target = os.environ.get(OBSERVE_ENV)
    if not target:
        return
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        config.pluginmanager.register(Observer(Path(target)), "split-observer")
    config.pluginmanager.register(SetupObserver(setups_path(Path(target), worker or LONE)),
                                  "split-setup-observer")
