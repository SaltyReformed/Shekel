"""The suite wrapper's name, its run to its end, and how a test ended from its phases'
reports: what every run of the wrapper shares, whoever starts it (``tools/split``'s record
and prove today).
"""
from __future__ import annotations

import signal
import subprocess
import tempfile
from pathlib import Path

#: The suite wrapper every run goes through, from the repository root: it gives each run a
#: database cluster of its own (never bare pytest).
WRAPPER = "./scripts/test.sh"


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
