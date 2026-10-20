"""A pytest plugin the split tool loads into a run (``-p tools.split._observe``) to record,
as JSON at the path ``$SHEKEL_SPLIT_OBSERVE`` names, what the run collected and deselected
and how every test ended.

**Why a plugin and not the JUnit report**: JUnit XML writes a non-strict XPASS as a plain
pass, so a test that started passing unexpectedly would read the same before and after.
This reads each phase's report itself, ``wasxfail`` included, on the process that sees
every report: the xdist controller, or the lone process under ``-n 0``.  Workers record
nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

#: The environment variable naming the file the observation is written to.
OBSERVE_ENV = "SHEKEL_SPLIT_OBSERVE"


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


def pytest_configure(config: pytest.Config) -> None:
    """Register an :class:`Observer` on the controller when ``$SHEKEL_SPLIT_OBSERVE`` is set."""
    target = os.environ.get(OBSERVE_ENV)
    if target and "PYTEST_XDIST_WORKER" not in os.environ:
        config.pluginmanager.register(Observer(Path(target)), "split-observer")
