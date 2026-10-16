"""The suite wrapper's door, :func:`tools.split._run.observe`, run to its end and stopped by
an exception, against STAND-INS for ``scripts/test.sh``; and the two readers of what a record
is judged in, :func:`tools.split._run.differences` and :func:`tools.split._run.environment`.

The tool suite starts no docker, so a stopped run is graded against a stand-in with the
wrapper's contract under ``SIGTERM``: a child started in the background (pytest's place),
and a ``TERM`` trap that stops it, waits for it, runs the teardown (here, a mark) and exits
143.  The real wrapper under the same signals was measured instead, 2026-10-15
(:func:`tools.split._run._wrapper_run`), and ``tests/test_scripts/test_test_runner.py``
holds that it traps ``TERM``.
"""
from __future__ import annotations

import importlib.metadata
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.ci import gitcmd, scratch
from tools.split import _run as run

#: ``scripts/test.sh``'s contract under ``SIGTERM``, standing in for it.  It writes its
#: child's pid to ``child`` (the trap is set first); on ``TERM`` it writes ``trapped``,
#: stops the child and waits for it, then runs its teardown, the ``torn`` mark, once
#: ``release`` exists (or 10 s passed), and exits 143.  The marks lie beside the observation
#: it is told to write, which it never writes.
STAND_IN = textwrap.dedent('''\
    #!/bin/sh
    marks="${SHEKEL_SPLIT_OBSERVE%/*}"
    trap 'echo > "$marks/trapped"; kill -TERM "$child"; wait "$child"
          waited=0
          until [ -e "$marks/release" ] || [ "$waited" -ge 200 ]; do
              sleep 0.05; waited=$((waited + 1))
          done
          echo > "$marks/torn"; exit 143' TERM
    sleep 30 &
    child=$!
    echo "$child" > "$marks/child"
    wait "$child"
    ''')


class Stop(BaseException):
    """What the case's ``SIGTERM`` handler raises: a ``BaseException``, as main's
    :class:`tools.split.split.Signalled` is."""


def _stop(_received: int, _frame: object) -> None:
    """The case's ``SIGTERM`` handler."""
    raise Stop


def _until(ready, what: str) -> None:
    """Wait up to 10 s for ``ready()`` to hold."""
    deadline = time.monotonic() + 10
    while not ready():
        assert time.monotonic() < deadline, f"{what} never happened"
        time.sleep(0.02)


def _alive(pid: int) -> bool:
    """Whether the process ``pid`` exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wrapper(root: Path, text: str) -> None:
    """Make ``text`` the repository's executable suite wrapper."""
    wrapper = root / run.WRAPPER
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(text, encoding="utf-8")
    wrapper.chmod(0o755)


@pytest.fixture(name="stand_in")
def _stand_in(tmp_path: Path):
    """A directory whose wrapper is :data:`STAND_IN`, and the directory its marks go in,
    with ``SIGTERM`` raising :class:`Stop` for the case; the stand-in's child stopped after
    the case if it outlived it (a mutant's), by the pid the stand-in wrote."""
    root, marks = tmp_path / "code", tmp_path / "marks"
    _wrapper(root, STAND_IN)
    marks.mkdir()
    previous = signal.signal(signal.SIGTERM, _stop)
    try:
        yield root, marks
    finally:
        signal.signal(signal.SIGTERM, previous)
        held = marks / "child"
        if held.exists() and _alive(int(held.read_text(encoding="utf-8"))):
            os.kill(int(held.read_text(encoding="utf-8")), signal.SIGKILL)


def _signal_at(marks: Path, *steps: str) -> threading.Thread:
    """Send ``SIGTERM`` to the main thread, where :func:`tools.split._run.observe` waits, once
    each mark of ``steps`` exists, in order."""
    main = threading.main_thread().ident

    def send() -> None:
        for step in steps:
            _until((marks / step).exists, f"the mark {step}")
            signal.pthread_kill(main, signal.SIGTERM)
    sender = threading.Thread(target=send)
    sender.start()
    return sender


def _observe(root: Path, marks: Path) -> None:
    """One run of the wrapper through the door."""
    run.observe(root, ["tests"], marks / "observed.json", collect_only=False)


def test_an_exception_stops_the_wrapper_through_its_own_teardown(stand_in):
    """The tool raises while the wrapper runs (here ``SIGTERM`` to the tool alone, raised as
    main raises it): the wrapper is sent ``SIGTERM`` and waited for, so its trap stopped its
    child and ran its teardown before the exception went on.  :func:`subprocess.run` sent it
    ``SIGKILL``, which no trap answers (the coordinator's ruling on condition (c), option 1)."""
    root, marks = stand_in
    (marks / "release").touch()
    sender = _signal_at(marks, "child")
    with pytest.raises(Stop):
        _observe(root, marks)
    sender.join()
    assert (marks / "torn").exists()
    assert not _alive(int((marks / "child").read_text(encoding="utf-8")))


def test_a_second_exception_abandons_the_wait_and_nothing_else(stand_in):
    """A second ``SIGTERM`` while the stopped wrapper is waited for: the wait is abandoned and
    the exception goes on at once, before the wrapper's teardown has run; the wrapper is not
    stopped again, and finishes its teardown by itself, its child gone (the ruling's
    condition (d))."""
    root, marks = stand_in
    sender = _signal_at(marks, "child", "trapped")
    with pytest.raises(Stop) as caught:
        _observe(root, marks)
    sender.join()
    assert not (marks / "torn").exists()
    assert isinstance(caught.value.__context__, Stop)
    (marks / "release").touch()
    _until((marks / "torn").exists, "the teardown")
    child = int((marks / "child").read_text(encoding="utf-8"))
    _until(lambda: not _alive(child), "the child's end")


def test_a_run_that_wrote_no_observation_shows_its_exit_and_output(tmp_path):
    """A wrapper that ran to its end and wrote no observation: refused with its exit status
    and what it wrote, its output and its errors in the order written."""
    root = tmp_path / "code"
    _wrapper(root, "#!/bin/sh\necho an-output-line\necho an-error-line >&2\nexit 3\n")
    with pytest.raises(run.RunError) as caught:
        run.observe(root, ["tests"], tmp_path / "observed.json", collect_only=True)
    assert str(caught.value).endswith(
        "wrote no observation (exit 3):\nan-output-line\nan-error-line")


def test_the_differences_are_every_path_once(tmp_path):
    """Each path at which the tree differs from HEAD, one entry a path: an edited, a deleted
    and a staged file, both paths of a rename, every file of an untracked directory (never
    the directory), a path holding a space and a letter outside ASCII (never quoted); never a
    file an ignore rule covers."""
    root = scratch.repository(tmp_path)
    for name in (".gitignore", "kept.txt", "edited.txt", "gone.txt", "old name.txt"):
        (root / name).write_text("ignored/\n" if name == ".gitignore" else name, encoding="utf-8")
    scratch.run(root, "add", "-A")
    scratch.run(root, "commit", "--quiet", "-m", "the base")
    (root / "edited.txt").write_text("changed", encoding="utf-8")
    (root / "gone.txt").unlink()
    scratch.run(root, "mv", "old name.txt", "new näme.txt")
    (root / "staged.txt").write_text("staged", encoding="utf-8")
    scratch.run(root, "add", "staged.txt")
    for name in ("fresh/a.py", "fresh/b.py", "ignored/x.py"):
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_text("", encoding="utf-8")
    assert run.differences(root) == {"edited.txt", "gone.txt", "old name.txt",
                                     "new näme.txt", "staged.txt", "fresh/a.py",
                                     "fresh/b.py"}


def test_asking_for_the_differences_writes_nothing(tmp_path):
    """git status refreshes the index when a file's times no longer match it, and rewrites it
    (measured 2026-10-15); ``--no-optional-locks`` keeps it from writing, so the index is
    byte-identical after the question."""
    root = scratch.repository(tmp_path)
    (root / "kept.txt").write_text("kept", encoding="utf-8")
    scratch.run(root, "add", "-A")
    scratch.run(root, "commit", "--quiet", "-m", "the base")
    earlier = time.time() - 100
    os.utime(root / "kept.txt", (earlier, earlier))
    index = (root / ".git" / "index").read_bytes()
    assert run.differences(root) == set()
    assert (root / ".git" / "index").read_bytes() == index


def _another_interpreter(monkeypatch) -> None:
    """``sys.version`` another build's."""
    monkeypatch.setattr(sys, "version", f"{sys.version} (another build)")


def _found(monkeypatch, change) -> None:
    """``importlib.metadata.distributions`` answering the real list as ``change`` makes it."""
    real = list(importlib.metadata.distributions())
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: change(real))


def _another_git(monkeypatch) -> None:
    """``git --version`` another release's."""
    real = gitcmd.run

    def answer(root, *args):
        done = real(root, *args)
        if args == ("--version",):
            return subprocess.CompletedProcess(done.args, 0, "git version 0.0.0\n", "")
        return done
    monkeypatch.setattr(gitcmd, "run", answer)


#: Each input of the environment, changed.
INPUTS = {
    "the interpreter's version": _another_interpreter,
    "a distribution added": lambda monkeypatch: _found(monkeypatch, lambda real: [
        *real, SimpleNamespace(name="another", version="1.0")]),
    "a distribution's version": lambda monkeypatch: _found(monkeypatch, lambda real: [
        SimpleNamespace(name=real[0].name, version=f"{real[0].version}.1"), *real[1:]]),
    "git's version": _another_git,
}


@pytest.mark.parametrize("change", INPUTS)
def test_the_environment_changes_with_each_of_its_inputs(tmp_path, monkeypatch, change):
    """The interpreter's version, each distribution's name and version, and git's version
    are each part of what a record keeps (REVISED (B) (2)): change one, and it is another
    environment."""
    held = run.environment(tmp_path)
    assert run.environment(tmp_path) == held
    INPUTS[change](monkeypatch)
    assert run.environment(tmp_path) != held


def test_the_order_distributions_are_found_in_is_no_difference(tmp_path, monkeypatch):
    """The same distributions found in another order are the same environment."""
    held = run.environment(tmp_path)
    _found(monkeypatch, lambda real: real[::-1])
    assert run.environment(tmp_path) == held
