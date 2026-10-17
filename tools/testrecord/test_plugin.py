"""The plugin's refusals, its core pinned against the environment, and which process writes
the outcomes (:mod:`tools.testrecord.plugin`).

coverage's default core on CPython 3.14 keeps no per-test context (measured 2026-10-16,
coverage 7.16.2: the second of two tests was recorded under none), so a run whose core is
any other than the C tracer is refused, and ``COVERAGE_CORE`` cannot choose another.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import NamedTuple

import coverage
import pytest

from tools.testrecord import plugin
from tools.testrecord.record import (
    ASSERTION_PLUGIN,
    PASS_HOOK,
    RECORD_ENV,
    ROOT_ENV,
    VERBOSITY,
    WHOLE_VALUES,
    RecordError,
    record,
)

#: The names the plugin registers its two recorders under.
RECORDERS = ("testrecord-tracer", "testrecord-outcomes")


class Unrecordable(NamedTuple):
    """A setting under which a passing assert is not recorded whole: what turns it off in a
    run (the environment, then pytest's arguments), and the words the refusal names it by."""

    env: dict[str, str]
    extra: tuple[str, ...]
    named: str


#: Each setting :func:`tools.testrecord.plugin.unrecordable` refuses, by its id.  The
#: assertion mode is turned off as delta review 4 measured it, through the environment.
UNRECORDABLE = {
    "assertion-plugin": Unrecordable({}, ("-p", f"no:{ASSERTION_PLUGIN}"),
                                     f"pytest's {ASSERTION_PLUGIN} plugin is not loaded"),
    "assertion-mode": Unrecordable({"PYTEST_ADDOPTS": "--assert=plain"}, (),
                                   "the assertion mode is 'plain', not 'rewrite' (--assert)"),
    "pass-hook": Unrecordable({}, ("-o", f"{PASS_HOOK}=false"), f"{PASS_HOOK} is off"),
    "assertion-verbosity": Unrecordable(
        {}, ("-o", f"{VERBOSITY}={WHOLE_VALUES - 1}"),
        f"the assertion verbosity is {WHOLE_VALUES - 1}, below {WHOLE_VALUES} ({VERBOSITY})"),
}


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


@pytest.mark.parametrize("case", list(UNRECORDABLE.values()), ids=list(UNRECORDABLE))
def test_a_run_that_cannot_record_every_passing_assert_whole_is_refused(project, out,
                                                                        monkeypatch, case):
    """A run under which a passing assert would go unrecorded, or be recorded shortened, is
    refused before any test runs, naming the setting (the coordinator's ruling on delta
    review 4's M-2, 2026-10-16), whatever the runner passed: the record's own settings are on
    its command line, and these come after them or from the environment.  The refusal is in
    the recorded run's own message, which ends with the wrapper's last lines (its n1), and it
    is the first thing the run wrote: no test ran before it (the branch's neutral review of
    M-2, L-4)."""
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RecordError) as caught:
        record(project, ["tests"], out, extra=(*case.extra, "-n", "0"))
    assert "wrote no outcomes (exit 4)" in str(caught.value)
    refusal = ("a recorded run records every passing assert whole, and under this run's "
               f"settings it cannot: {case.named}")
    assert refusal in str(caught.value)
    assert (out / "wrapper-output.txt").read_text(encoding="utf-8").startswith(
        f"ERROR: {refusal}")


def test_a_run_that_records_nothing_is_never_refused_for_its_settings(monkeypatch):
    """Loaded without ``$SHEKEL_TESTRECORD``, the plugin records nothing, so it judges no
    setting: the configuration it is handed is never read."""
    monkeypatch.delenv(RECORD_ENV, raising=False)
    plugin.pytest_configure(SimpleNamespace())
