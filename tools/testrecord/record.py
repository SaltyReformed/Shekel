"""A recorded run of the suite and what it records, per test (ruling ``balance:R-BAL303``):
:func:`record` runs tests through the suite wrapper with :mod:`tools.testrecord.plugin`
loaded and reads back, for each test, how it ended, the arcs each of its phases executed in
:data:`MEASURED`, the passing asserts of each phase that pytest rewrote (the plugin names
which those are), in order, and the exception that ended each phase that raised one.  It
also holds what every run of the wrapper (:data:`tools.ci.arcs.WRAPPER`) shares, whoever
starts it: the environment it starts in (:func:`wrapper_env`), its run to its end
(:func:`wrapper_run`), the last lines of what it wrote that a refusal shows
(:func:`wrapper_tail`, through :func:`tools.ci.text.shown`), and how a test ended from its
phases' reports (:func:`verdict`).

A run writes into a directory made for it, which must be empty: each process's files, the
wrapper's whole output (``wrapper-output.txt``, kept as written, for a refusal to point
at), and the run order when one is given.  pytest's rewritten test modules are written
under a fresh ``PYTHONPYCACHEPREFIX`` inside it, removed once the wrapper has returned (a
run an exception stops leaves it in that run's directory, which no later run may reuse):
the name of a rewritten module's cached file does not say whether the assert-pass hook was
enabled when it was rewritten, so a cached one could lack the hook's calls.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.ci.arcs import WRAPPER
from tools.ci import text as ci_text

#: The environment variable naming the directory every process of a run writes into.
RECORD_ENV = "SHEKEL_TESTRECORD"

#: The environment variable naming the root the run starts in, resolved: what the plugin
#: measures under, and what pytest's rootdir, which names the tests, must be.
ROOT_ENV = "SHEKEL_TESTRECORD_ROOT"

#: The environment variable naming a JSON list of node ids: when set, the lone process runs
#: exactly those tests, in that order.
ORDER_ENV = "SHEKEL_TESTRECORD_ORDER"

#: The variable xdist names a worker by, and the two others it sets in one
#: (``xdist/remote.py``).  A run started from inside a worker would inherit all three, each
#: describing that worker's session rather than the run's: with the first the run takes
#: itself for the worker and writes no outcomes, and with the run id alone
#: ``tests/conftest.py`` takes it for an xdist controller and skips its database bootstrap.
#: So :func:`wrapper_env` leaves them out.
WORKER = "PYTEST_XDIST_WORKER"
XDIST_IDENTITY = (WORKER, "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID")

#: The xdist option that runs every test in ONE process, in its order: what a run given an
#: order passes (:func:`record`), and what a caller passes to run a layout natively in one
#: process (the split tool's traced source runs).
ONE_PROCESS = ("-n", "0")

#: The directories measured, relative to the root the run starts in.
MEASURED = ("app", "tests")

#: The process name a run without xdist workers writes under.
LONE = "main"

#: The coverage core every recorded run measures with: the only one keeping per-test
#: contexts (:mod:`tools.testrecord.plugin`).
CORE = "CTracer"

#: The hash seed every process that inherits the environment of a run of the wrapper the tools
#: start hashes with (``PYTHONHASHSEED``; ``0`` turns hash randomization off), whatever the
#: caller's environment holds (:func:`wrapper_env`): a recorded run's, and the split tool's outcome
#: runs (:func:`tools.split._run.observe`).  A string's or a date's hash is seeded per process, so
#: a set's iteration order differs run to run, and a record followed it: the R-BAL303 pilot
#: (2026-10-16) measured a set's repr in an assert's explanation (``{'raises', 'periods', 'today',
#: 'thirds'}`` against ``{'thirds', 'periods', 'raises', 'today'}``), and app control flow, where
#: ``escrow_calculator._escrow_unchanged_by_merge`` takes ``all()`` over a set of dates and stops
#: at the first False, so ``TestEscrow::test_merge_rejects_overlap`` ran a line of
#: ``app/utils/effective_dated.py`` in one run only.  Two RECORDS of one tree, and of a split's two
#: layouts, must hash alike (the coordinator's ruling on the pilot's Fork 2), and so must a
#: record's outcome runs and a prove's, which compare one layout's outcomes and fixture setups with
#: the other's (the coordinator's ruling on item (4)'s Fork 2, 2026-10-16).  What no seed fixes: an
#: object hashed by its identity (``object.__hash__`` derives from its address), and a string
#: holding the layout or the run (a node id, a module path, ``tmp_path``'s directory), each of
#: which may still iterate differently between layouts; a proof sees that as a difference, and
#: refuses.
HASH_SEED = "0"

#: How many of the last lines a run of the wrapper wrote a refusal shows (:func:`wrapper_tail`).
TAIL_LINES = 30

#: The three phases of a test, in order.
PHASES = ("setup", "call", "teardown")

#: pytest's assertion plugin, by the name ``-p no:`` blocks it under; the ini setting under
#: which pytest reports each assert it rewrote that passes (``pytest_assertion_pass``); and
#: the ini setting of the assertion verbosity, and the verbosity from which pytest shows each
#: side of an explanation's first line whole and cuts no explanation short (pytest 9.1.1's
#: ``_get_maxsize_for_saferepr`` and ``truncate._get_truncation_parameters``; the detail lines
#: below the first keep ``saferepr``'s default limit at any verbosity).  The plugin refuses a
#: run under which any of them does not hold (:func:`tools.testrecord.plugin.unrecordable`).
ASSERTION_PLUGIN = "assertion"
PASS_HOOK = "enable_assertion_pass_hook"
VERBOSITY = "verbosity_assertions"
WHOLE_VALUES = 2

#: What the plugin is loaded as, and the settings every recorded run passes on the command
#: line, never through ``pytest.ini``: the assert-pass hook enabled, and the assertion
#: verbosity at which values are shown whole.
PLUGIN = "tools.testrecord.plugin"
SETTINGS = ("-o", f"{PASS_HOOK}=true", "-o", f"{VERBOSITY}={WHOLE_VALUES}")

#: The pytest exit statuses a session that ran to its end exits with: every test passed, or
#: some failed.  Any other (interrupted, an internal error, a usage error, nothing collected)
#: recorded no session of the tests.  Necessary, not sufficient: a session ``-x`` or
#: ``--maxfail`` stopped early, and one that went on past a collector that failed, exit as
#: one whose tests failed, so :func:`read` also refuses a failed collector and a collected
#: test that never ran.
COMPLETED = frozenset({pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED})


class RecordError(RuntimeError):
    """A recorded run did not record what it exists to record."""


@dataclass(frozen=True)
class Trace:
    """One test as a run recorded it: how it ended; per phase, the arcs it executed, by
    file relative to the root; per phase, its passing asserts that pytest rewrote, in the
    order they passed, each its line, its source text and pytest's explanation; and for each
    phase that raised an exception, the exception that ended it, as its type's qualified name
    and its message (:mod:`tools.testrecord.plugin` says what that holds and what it does
    not)."""

    outcome: str
    arcs: dict[str, dict[str, frozenset[tuple[int, int]]]]
    asserts: dict[str, tuple[tuple[int, str, str], ...]]
    raised: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class Record:
    """One run's record: every test that ran, by node id; the order the lone process ran
    them in (empty under xdist); and the session's exit status."""

    tests: dict[str, Trace]
    order: tuple[str, ...]
    exitstatus: int


def process_name() -> str:
    """This process's name: its xdist worker id, or :data:`LONE`."""
    return os.environ.get(WORKER, LONE)


def trace_path(directory: Path, process: str) -> Path:
    """Where ``process`` writes the traces and asserts of the tests it ran."""
    return directory / f"trace-{process}.json"


def outcomes_path(directory: Path) -> Path:
    """Where the process that sees every report writes how each test ended."""
    return directory / "outcomes.json"


def wrapper_run(args: list[str], root: Path, env: dict[str, str]) -> tuple[int, bytes]:
    """Run the suite wrapper to its end: its exit status, and what it wrote to its output
    and its errors, as one stream.

    Any exception while it runs (Ctrl-C, the ``SIGTERM`` or ``SIGHUP`` that
    :func:`tools.split.split.main` raises, anything) stops it with ``SIGTERM``, which its
    trap answers by stopping pytest, waiting for it and removing its database cluster
    (``scripts/test.sh``); then the wrapper is waited for, and the exception goes on.  So no
    pytest it started outlives this process to write an observation, and no cluster is left
    running.  The wrapper is never sent ``SIGKILL``, the one signal its trap cannot answer
    (the coordinator's ruling on condition (c), option 1, 2026-10-15): :func:`subprocess.run`,
    which this replaced, sends exactly that to its child on any exception, after a 0.25 s
    grace for Ctrl-C.  Measured that day through :func:`tools.split._run.observe`, against
    the real wrapper and a test that sleeps: under a ``SIGTERM`` to the tool alone, and under
    a Ctrl-C to its whole process group, the pytest was re-parented to PID 1, wrote its
    observation after the tool had stopped, and the cluster was still up 30 s later; stopped
    as here (this code, under both), pytest was gone within a second, the cluster removed
    and nothing written.

    The output goes to a file, not a pipe, so the wrapper never waits on a reader: a second
    exception while the wrapper is waited for abandons the wait and nothing else, and the
    wrapper, already stopping, finishes its teardown writing to a file still open to it.

    The guarantee holds only for what reaches this process as an EXCEPTION.  Ctrl-C does;
    ``SIGTERM`` and ``SIGHUP`` do only once the caller has installed handlers raising them
    (:func:`tools.split.split.main` installs them for every command).  At their default
    disposition Python ends at once, no ``except`` runs, and the wrapper's pytest and its
    cluster outlive the process: the 2026-10-15 measurement above."""
    with tempfile.TemporaryFile() as output:
        # Pylint: consider-using-with -- a with statement's exit waits for the wrapper
        # again, so a second exception could not abandon the wait.
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            args, cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            proc.wait()
        except BaseException:
            proc.send_signal(signal.SIGTERM)
            proc.wait()
            raise
        output.seek(0)
        return proc.returncode, output.read()


def wrapper_tail(output: bytes) -> str:
    """The last :data:`TAIL_LINES` lines of what a run of the wrapper wrote
    (:func:`wrapper_run`), shown (:func:`tools.ci.text.shown`): what a refusal of a run that
    wrote nothing prints, a recorded run's (:func:`record`) and an outcome run's
    (:func:`tools.split._run.observe`) alike (the coordinator's ruling on delta review 4's
    n1, 2026-10-16).  Its lines are split by :func:`tools.ci.text.lines_of`, so a line
    holding a character :meth:`str.splitlines` ends a line at and Python does not (a form
    feed in an exception's message, as pytest prints it on its ``E`` line and in its short
    summary) is shown whole and counted once (the coordinator's ruling on the lane review's
    M-B, 2026-10-17)."""
    return "\n".join(ci_text.lines_of(ci_text.shown(output))[-TAIL_LINES:])


def verdict(phases: dict[str, tuple[str, bool]]) -> str:
    """One test's outcome from its phases' ``(outcome, wasxfail)``: ``passed``, ``failed``,
    ``error``, ``skipped``, ``xfailed`` or ``xpassed``, or ``not-run`` when no call was
    reported and its setup neither failed nor skipped, with ``+teardown-error`` appended when
    its teardown failed."""
    setup, call, teardown = (phases.get(w) for w in PHASES)
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


def wrapper_env(root: Path) -> dict[str, str]:
    """The environment every run of the suite wrapper the tools start runs in, before its
    caller adds the variables of its own run: this process's, without the xdist identity
    (:data:`XDIST_IDENTITY`) or a run order (:data:`ORDER_ENV`), neither of which is the
    run's; hashed with :data:`HASH_SEED`; and ``root`` first on ``PYTHONPATH``, so a plugin
    the run loads is imported from the tree it runs.  The ONE builder (the coordinator's
    ruling on item (4)'s Fork 2, 2026-10-16): :func:`record` and the split tool's outcome
    runs (:func:`tools.split._run.observe`) built it two ways, and only the first dropped the
    identity and the run order and pinned the seed, so an outcome run started from inside an
    xdist worker took itself for that worker and wrote no observation, and a record's outcome
    runs and a prove's could hash apart: each process drew its own seed unless the caller
    exported one."""
    env = {name: value for name, value in os.environ.items()
           if name not in (*XDIST_IDENTITY, ORDER_ENV)}
    env["PYTHONHASHSEED"] = HASH_SEED
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(root), env.get("PYTHONPATH")) if p)
    return env


def record(root: Path, targets: list[str], out: Path, order: list[str] | None = None,
           extra: tuple[str, ...] = ()) -> Record:
    """Run ``targets`` through the suite wrapper in ``root`` with the plugin loaded, writing
    into ``out``, an empty directory made for this run, and read back what it recorded.
    ``order``, when given, runs exactly those tests in that order in one process (``-n 0``);
    ``extra`` is passed to pytest after everything else.  Refused as :func:`read` refuses a
    record, when the run wrote no outcomes, and when an order was given and the tests ran in
    another.  The wrapper starts in :func:`wrapper_env`'s environment, so the wrapper,
    pytest, each xdist worker and every child that inherits their environment hash with
    :data:`HASH_SEED`, and none inherits this process's run order or xdist identity; a test
    that builds its child's environment from a list of names (the seed scripts' tests in
    ``tests/test_scripts``) starts that child unseeded.  Stopped as :func:`wrapper_run` stops
    the wrapper, so only on an exception: a caller that must be stopped by ``SIGTERM`` or
    ``SIGHUP`` turns them into exceptions first."""
    if not out.is_dir() or any(out.iterdir()):
        raise RecordError(f"{out} is not an empty directory made for this run")
    if order is not None and any(arg.startswith(("-n", "--numprocesses")) for arg in extra):
        raise RecordError("a run order runs in one process; it takes no -n of its own")
    pycache = out / "pycache"
    env = wrapper_env(root)
    env[RECORD_ENV] = str(out)
    env[ROOT_ENV] = str(root.resolve())
    env["PYTHONPYCACHEPREFIX"] = str(pycache)
    args = [WRAPPER, *targets, "-p", PLUGIN, "-p", "no:cacheprovider", "-q", *SETTINGS]
    if order is not None:
        held = out / "order.json"
        held.write_text(json.dumps(order), encoding="utf-8")
        env[ORDER_ENV] = str(held)
        args += ONE_PROCESS
    args += list(extra)
    returncode, output = wrapper_run(args, root, env)
    (out / "wrapper-output.txt").write_bytes(output)
    if pycache.exists():
        shutil.rmtree(pycache)
    if not outcomes_path(out).exists():
        raise RecordError(f"{' '.join(args)} wrote no outcomes (exit {returncode}); its "
                          f"output is {out / 'wrapper-output.txt'}, ending:\n"
                          + wrapper_tail(output))
    found = read(out)
    if order is not None and found.order != tuple(order):
        raise RecordError(f"{' '.join(args)} ran its tests in another order than the one "
                          "asked for: a hook reordered them after the order was applied")
    return found


def _processes(out: Path) -> tuple[dict[str, dict], set[str], dict[str, str], list[str]]:
    """What the processes of the run in ``out`` wrote: each test some process ran, with its
    arcs, asserts and the exceptions its phases raised; every test any of them collected;
    each test whose call ran, with the file its function is in; and the order the lone
    process ran its tests in (empty when none did).  Refused when a test ran in two processes,
    and when a process's file holds no exceptions: the recorder wrote none before it kept
    them (the coordinator's ruling on delta review 4's H-1, 2026-10-16), so a record taken
    before then is no record of what a failing test compared (the branch's neutral review of
    H-1, L4)."""
    traced: dict[str, dict] = {}
    collected: set[str] = set()
    called: dict[str, str] = {}
    order: list[str] = []
    for path in sorted(out.glob(trace_path(out, "*").name)):
        process = json.loads(path.read_text(encoding="utf-8"))
        if "raised" not in process:
            raise RecordError(f"{path} holds no exceptions: a recorder that did not keep them "
                              "wrote it; record again")
        twice = set(process["ran"]) & set(traced)
        if twice:
            raise RecordError(f"{len(twice)} test(s) ran in two processes, e.g. "
                              f"{sorted(twice)[:3]}")
        for nodeid in process["ran"]:
            traced[nodeid] = {"arcs": process["traces"].get(nodeid, {}),
                              "asserts": process["asserts"].get(nodeid, {}),
                              "raised": process["raised"].get(nodeid, {})}
        collected.update(process["collected"])
        called.update(process["called"])
        if path == trace_path(out, LONE):
            order = process["ran"]
    return traced, collected, called, order


def read(out: Path) -> Record:
    """The record a run wrote into ``out``.  Refused unless its session ran to its end with
    no collector failing, every test it collected ran in exactly one process that traced it
    and reported how it ended, and every test whose call ran measured a line of the file its
    function is in.  A call that measured nothing of its own file lost its measurement: a
    trace function displaced, or files measured other than the ones run.  The one shape
    refused though measured is a test whose call never runs its function's body (an
    ``async def`` test with no plugin to run it, or a doctest); ``tests/`` holds neither
    (census 2026-10-16: no ``async def test_``, no doctest option or doctest)."""
    held = json.loads(outcomes_path(out).read_text(encoding="utf-8"))
    if held["exitstatus"] not in COMPLETED:
        raise RecordError(f"the session in {out} did not run to its end "
                          f"(exit status {held['exitstatus']})")
    if held["collect_errors"]:
        raise RecordError(f"the session in {out} failed to collect "
                          f"{len(held['collect_errors'])} collector(s), e.g. "
                          f"{held['collect_errors'][:3]}")
    traced, collected, called, order = _processes(out)
    outcomes = held["outcomes"]
    untraced = sorted(set(outcomes) ^ set(traced))
    if untraced:
        raise RecordError(f"{len(untraced)} test(s) have an outcome and no trace, or a trace "
                          f"and no outcome, e.g. {untraced[:3]}")
    unrun = sorted(collected ^ set(outcomes))
    if unrun:
        raise RecordError(f"the session in {out} did not run every test it collected: "
                          f"{len(unrun)} test(s) collected and not run, or run and not "
                          f"collected, e.g. {unrun[:3]}")
    lost = sorted((nodeid, file) for nodeid, file in called.items()
                  if file not in traced[nodeid]["arcs"].get(PHASES[1], {}))
    if lost:
        raise RecordError(f"{len(lost)} test(s) ran their call with no line of their own file "
                          "measured, so the measurement was lost, e.g. "
                          + ", ".join(f"{nodeid} in {file}" for nodeid, file in lost[:3]))
    return Record(tests={nodeid: Trace(
        outcome=outcomes[nodeid],
        arcs={phase: {path: frozenset(tuple(arc) for arc in arcs)
                      for path, arcs in files.items()}
              for phase, files in traced[nodeid]["arcs"].items()},
        asserts={phase: tuple(tuple(entry) for entry in entries)
                 for phase, entries in traced[nodeid]["asserts"].items()},
        raised={phase: tuple(entry) for phase, entry in traced[nodeid]["raised"].items()})
        for nodeid in sorted(traced)}, order=tuple(order), exitstatus=held["exitstatus"])
