"""The plugin's refusals, its core pinned against the environment, and which process writes
the outcomes (:mod:`tools.testrecord.plugin`).

coverage's default core on CPython 3.14 keeps no per-test context (measured 2026-10-16,
coverage 7.16.2: the second of two tests was recorded under none), so a run whose core is
any other than the C tracer is refused, and ``COVERAGE_CORE`` cannot choose another.
"""
from __future__ import annotations

from types import SimpleNamespace

import coverage
import pytest

from tools.testrecord import plugin
from tools.testrecord.record import RECORD_ENV, ROOT_ENV, record

#: The names the plugin registers its two recorders under.
RECORDERS = ("testrecord-tracer", "testrecord-outcomes")


def test_a_core_that_keeps_no_context_is_refused(tmp_path, monkeypatch):
    """A measurement that reports another core is stopped and refused."""
    monkeypatch.setattr(coverage.Coverage, "sys_info",
                        lambda self: [("core", "SysMonitor")])
    running = coverage.Coverage.current()
    with pytest.raises(pytest.UsageError, match="'SysMonitor', not 'CTracer'"):
        plugin.Tracer(tmp_path, tmp_path, "main")
    assert coverage.Coverage.current() is running


@pytest.mark.parametrize("worker, registered", [(None, set(RECORDERS)),
                                                ("gw0", {RECORDERS[0]})],
                         ids=["lone-or-controller", "worker"])
def test_only_the_process_that_sees_every_report_writes_the_outcomes(tmp_path, monkeypatch,
                                                                     worker, registered):
    """Every process traces the tests it runs; an xdist worker writes no outcomes, since the
    controller hears every report and writes them once every worker has ended."""
    monkeypatch.setenv(RECORD_ENV, str(tmp_path))
    monkeypatch.setenv(ROOT_ENV, str(tmp_path.resolve()))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    if worker:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    monkeypatch.setattr(plugin, "Tracer", lambda *args: SimpleNamespace())
    manager = pytest.PytestPluginManager()
    plugin.pytest_load_initial_conftests(SimpleNamespace(pluginmanager=manager,
                                                         rootpath=tmp_path))
    assert {name for name in RECORDERS if manager.has_plugin(name)} == registered


def test_a_test_collected_twice_is_refused():
    """Two collected tests with one node id cannot each be named once by an order."""
    items = [SimpleNamespace(nodeid=nodeid) for nodeid in ("a", "a", "b")]
    with pytest.raises(pytest.UsageError, match="exactly once"):
        plugin.ordered(items, ["a", "b"])


def test_the_environments_core_does_not_choose_the_core(project, out, monkeypatch):
    """Under ``COVERAGE_CORE=sysmon`` each parametrized case still holds its own arcs: the
    core is set after the configuration that variable feeds."""
    monkeypatch.setenv("COVERAGE_CORE", "sysmon")
    found = record(project, ["tests"], out, extra=("-n", "0"))
    assert found.tests["tests/test_sample.py::test_branch[3]"].arcs["call"][
        "app/calc.py"] == {(-1, 2), (2, 3), (3, -1)}


@pytest.mark.parametrize("order", [
    ["a"], ["a", "b", "c"], ["a", "a", "b"], ["a", "c"]],
    ids=["one-omitted", "one-not-collected", "one-twice", "one-swapped-for-another"])
def test_an_order_naming_another_set_is_refused(order):
    """An order must name each collected test exactly once."""
    items = [SimpleNamespace(nodeid=nodeid) for nodeid in ("a", "b")]
    with pytest.raises(pytest.UsageError, match="exactly once"):
        plugin.ordered(items, order)
