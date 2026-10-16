"""The suite wrapper's door, :func:`tools.split._run.observe`, run to its end and stopped by
an exception, against STAND-INS for ``scripts/test.sh``; and the two readers of what a record
is judged in, :func:`tools.split._run.differences` and :func:`tools.split._run.environment`.

The tool suite starts no docker, so a stopped run is graded against a stand-in with the
wrapper's contract under ``SIGTERM``: a child started in the background (pytest's place),
and a ``TERM`` trap that stops it, waits for it, runs the teardown (here, a mark) and exits
143.  The real wrapper under the same signals was measured instead, 2026-10-15
(:func:`tools.testrecord.record.wrapper_run`), and ``tests/test_scripts/test_test_runner.py``
holds that it traps ``TERM``.
"""
from __future__ import annotations

import importlib.metadata
import ast
import os
import re
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
from tools.ci.arcs import REPO
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


def _base(parent: Path) -> Path:
    """A repository whose ``HEAD`` holds ``kept.txt``, ``sub/b.txt`` and a ``.gitignore``
    naming ``cache/``, with a tree that is ``HEAD``'s; its root."""
    root = scratch.repository(parent)
    for name, text in {".gitignore": "cache/\n", "kept.txt": "kept\n", "sub/b.txt": "b\n"}.items():
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    scratch.run(root, "add", "-A")
    scratch.run(root, "commit", "--quiet", "-m", "the base")
    return root


def _flagged(flag: str):
    """``kept.txt`` flagged in the index with ``flag``, then edited."""
    def change(root: Path) -> None:
        scratch.run(root, "update-index", flag, "kept.txt")
        (root / "kept.txt").write_text("edited\n", encoding="utf-8")
    return change


def _hidden_by(where: str):
    """An untracked ``new.py``, named by a per-machine rule: ``.git/info/exclude``, or a file
    ``core.excludesFile`` names."""
    def change(root: Path) -> None:
        (root / "new.py").write_text("", encoding="utf-8")
        if where == "info/exclude":
            (root / ".git" / "info").mkdir(exist_ok=True)
            (root / ".git" / "info" / "exclude").write_text("new.py\n", encoding="utf-8")
        else:
            rules = root.parent / "machine-ignore"
            rules.write_text("new.py\n", encoding="utf-8")
            scratch.run(root, "config", "core.excludesFile", str(rules))
    return change


def _beside(root: Path, directory: str) -> None:
    """An untracked ``.gitignore`` holding ``*`` in ``directory``, beside an untracked file it
    hides with itself."""
    (root / directory).mkdir()
    (root / directory / ".gitignore").write_text("*\n", encoding="utf-8")
    (root / directory / "conftest.py").write_text("", encoding="utf-8")


def _mode_unseen(root: Path) -> None:
    """``kept.txt`` made executable where ``core.fileMode`` tells git not to look."""
    scratch.run(root, "config", "core.fileMode", "false")
    (root / "kept.txt").chmod(0o755)


def _a_directory(root: Path) -> None:
    """An executable ``run.sh`` committed, then replaced by a directory of that name, whose
    mode an owner may execute too: only its kind shows it is no file."""
    (root / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "run.sh").chmod(0o755)
    scratch.run(root, "add", "run.sh")
    scratch.run(root, "commit", "--quiet", "-m", "an executable")
    (root / "run.sh").unlink()
    (root / "run.sh").mkdir()


#: Each way a tree is not ``HEAD``'s that ``git status`` did not show, or a file it never
#: reads, and what :func:`tools.split._run.differences` lists (the coordinator's rulings on
#: delta review 3's H-1 and on the thirteenth unit correction, 2026-10-16).
SHAPES = {
    "edited under --skip-worktree": (_flagged("--skip-worktree"), {"kept.txt"}),
    "edited under --assume-unchanged": (_flagged("--assume-unchanged"), {"kept.txt"}),
    "left out by a sparse checkout": (
        lambda root: scratch.run(root, "sparse-checkout", "set"), {"sub/b.txt"}),
    "hidden by .git/info/exclude": (_hidden_by("info/exclude"), {"new.py"}),
    "hidden by core.excludesFile": (_hidden_by("core.excludesFile"), {"new.py"}),
    "hidden by an untracked .gitignore": (lambda root: _beside(root, "area"),
                                          {"area/.gitignore"}),
    "an untracked .gitignore in a directory a rule ignores": (
        lambda root: _beside(root, "cache"), set()),
    "made executable, core.fileMode false": (_mode_unseen, {"kept.txt"}),
    "an executable replaced by a directory": (_a_directory, {"run.sh"}),
}


@pytest.mark.parametrize("shape", SHAPES)
def test_no_index_flag_or_per_machine_rule_hides_a_difference(tmp_path, shape):
    """Each path is judged by its file's bytes and mode against ``HEAD``, and each untracked
    file by the ``.gitignore`` files alone: an index flag, a sparse checkout, a per-machine
    ignore rule and ``core.fileMode`` each hid one from ``git status``.  An untracked
    ``.gitignore`` is itself listed, because its rules are not ``HEAD``'s; one git never
    reads, inside a directory a committed rule ignores (``.pytest_cache``'s), is not."""
    root = _base(tmp_path)
    change, listed = SHAPES[shape]
    change(root)
    assert run.differences(root) == listed


def _symlink(root: Path) -> None:
    """A symlink committed beside ``kept.txt``."""
    (root / "link").symlink_to("kept.txt")
    scratch.run(root, "add", "link")
    scratch.run(root, "commit", "--quiet", "-m", "a symlink")


def _gitlink(root: Path) -> None:
    """A submodule committed at ``sub module``, naming the base commit."""
    head = scratch.run(root, "rev-parse", "HEAD")
    scratch.run(root, "update-index", "--add", "--cacheinfo", f"160000,{head},sub module")
    scratch.run(root, "commit", "--quiet", "-m", "a submodule")


@pytest.mark.parametrize("change,path", [(_symlink, "link"), (_gitlink, "sub module")],
                         ids=["a symlink", "a submodule"])
def test_a_path_head_holds_as_no_regular_file_is_not_modelled(tmp_path, change, path):
    """git hashes a symlink's target and cannot hash a submodule's files, so a ``HEAD`` that
    holds either is refused by name rather than judged wrongly (the coordinator's condition
    on the thirteenth unit correction)."""
    root = _base(tmp_path)
    change(root)
    with pytest.raises(run.RunError, match=re.escape(
            f"HEAD holds 1 path(s) as a symlink or a submodule, e.g. ['{path}']: not "
            "modelled")):
        run.differences(root)


def _another_interpreter(monkeypatch, _root: Path) -> None:
    """``sys.version`` another build's."""
    monkeypatch.setattr(sys, "version", f"{sys.version} (another build)")


def _found(monkeypatch, change) -> None:
    """``importlib.metadata.distributions`` answering the real list as ``change`` makes it."""
    real = list(importlib.metadata.distributions())
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: change(real))


def _another_git(monkeypatch, _root: Path) -> None:
    """``git --version`` another release's."""
    real = gitcmd.run

    def answer(root, *args):
        done = real(root, *args)
        if args == ("--version",):
            return subprocess.CompletedProcess(done.args, 0, "git version 0.0.0\n", "")
        return done
    monkeypatch.setattr(gitcmd, "run", answer)


def _written(where: str):
    """A file written at ``where``, relative to the repository root (``..`` above it)."""
    def change(_monkeypatch, root: Path) -> None:
        (root / where).parent.mkdir(exist_ok=True)
        (root / where).write_text("SAMPLE=1\n", encoding="utf-8")
    return change


#: Each input of the environment, changed.
INPUTS = {
    "the interpreter's version": _another_interpreter,
    "a distribution added": lambda monkeypatch, _root: _found(monkeypatch, lambda real: [
        *real, SimpleNamespace(name="another", version="1.0")]),
    "a distribution's version": lambda monkeypatch, _root: _found(monkeypatch, lambda real: [
        SimpleNamespace(name=real[0].name, version=f"{real[0].version}.1"), *real[1:]]),
    "git's version": _another_git,
    "a .env where app/config.py looks first": _written("app/.env"),
    "a .env at the root": _written(".env"),
    "a .env above the repository": _written("../.env"),
    "a .env.dev at the root": _written(".env.dev"),
}


@pytest.mark.parametrize("change", INPUTS)
def test_the_environment_changes_with_each_of_its_inputs(tmp_path, monkeypatch, change):
    """The interpreter's version, each distribution's name and version, git's version, and
    each file a run reads that the committed ignore rules hide are each part of what a record
    keeps (REVISED (B) (2); delta review 3's M-2): change one, and it is another
    environment.  ``load_dotenv()`` reads the first ``.env`` from ``app/`` up to the
    filesystem's root, so one above the repository counts too."""
    root = tmp_path / "repo"
    root.mkdir()
    held = run.environment(root)
    assert run.environment(root) == held
    INPUTS[change](monkeypatch, root)
    assert run.environment(root) != held


def test_a_file_a_run_reads_is_held_by_its_bytes(tmp_path):
    """Its bytes, not its presence: ``.env`` edited is another environment, and so is
    ``.env`` gone, or a directory in its place."""
    root = tmp_path / "repo"
    root.mkdir()
    env = root / ".env"
    seen = []
    for change in (lambda: env.write_text("A=1\n", encoding="utf-8"),
                   lambda: env.write_text("A=2\n", encoding="utf-8"), env.unlink, env.mkdir):
        change()
        seen.append(run.environment(root))
    assert len(set(seen)) == 4


def test_the_order_distributions_are_found_in_is_no_difference(tmp_path, monkeypatch):
    """The same distributions found in another order are the same environment."""
    held = run.environment(tmp_path)
    _found(monkeypatch, lambda real: real[::-1])
    assert run.environment(tmp_path) == held


#: Each module the census below names that does not read the repository's copy of a file
#: the committed ignore rules hide, and why (an entry ending in ``/`` covers a directory).
NOT_READERS = {
    "tests/conftest.py": "it writes the fixture profile's files only under "
                         "SHEKEL_TEST_FIXTURE_PROFILE=1, and reads them back for a printed "
                         "table, never for an outcome",
    "tests/manual/": "pytest never collects it: pytest.ini's python_files is test_*.py, and "
                     "these are verify_*, save_* and shoot.py",
    "tests/test_deploy/test_shekel_deploy_behaviour.py": "a .env it writes under tmp_path",
    "tests/test_scripts/test_ops_shell_readers.py": "a .env it writes under tmp_path",
    "tools/playwright/shekel_shot.py": "a screenshot tool no test and no record runs",
    "tools/split/test_dynamic.py": "a build/ in a scratch repository",
    "tools/split/test_run.py": "a .env it writes under tmp_path",
    "tools/split/test_split.py": "a build/ in a scratch repository",
}

#: python-dotenv's readers, by the name a module calls.
_DOTENV = {"load_dotenv", "dotenv_values", "find_dotenv"}


def _ignored_names(root: Path) -> list[str]:
    """Each entry of ``root``'s ``.gitignore`` that holds no glob character, without its
    leading or trailing slash: the names a module joins to a path to read an ignored file."""
    return [line.strip().strip("/") for line in
            (root / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#") and not set(line) & set("*?[!")]


def _names_one(text: str, names: list[str]) -> bool:
    """Whether the path ``text`` is one of ``names``, lies inside one, or ends with one."""
    held = text.strip("/")
    return any(held == name or held.startswith(f"{name}/") or held.endswith(f"/{name}")
               or name.endswith(f"/{held}") for name in names)


def _reads_ignored(path: Path, names: list[str]) -> bool:
    """Whether the module ``path`` calls python-dotenv's readers, or joins a path to one of
    ``names`` with ``/`` (the two shapes this tree's readers take: ``load_dotenv()`` and
    ``REPO_ROOT / ".env.dev"``)."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and (
                getattr(node.func, "attr", None) or getattr(node.func, "id", None)) in _DOTENV:
            return True
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
                and _names_one(node.right.value, names)):
            return True
    return False


def census(root: Path) -> list[str]:
    """Every module under ``app/``, ``tests/``, ``scripts/`` and ``tools/`` of the tree
    (:func:`tools.split._run.tree_files`) that :func:`_reads_ignored` names."""
    names = _ignored_names(root)
    return sorted(str(path.relative_to(root)) for path in run.tree_files(root)
                  if path.suffix == ".py" and path.relative_to(root).parts[0] in
                  {"app", "tests", "scripts", "tools"} and _reads_ignored(path, names))


def _covers(entry: str, module: str) -> bool:
    """Whether the entry ``entry`` (a module, or a directory ending in ``/``) covers
    ``module``."""
    return module.startswith(entry) if entry.endswith("/") else module == entry


def test_every_module_reading_an_ignored_file_is_held():
    """A census of this repository's tree: every module that reads a file the committed
    ignore rules hide is a reader the environment holds (:data:`tools.split._run.
    IGNORED_READERS`) or named here as no reader, with why; and every entry of both still
    names a module the census finds (the coordinator's ruling on delta review 3's M-2,
    2026-10-16: ``.env``, read by ``app/config.py`` on import, was in neither half of a
    record's state).  The census sees a python-dotenv call and a path joined with ``/``, the
    two shapes this tree's readers take; ``open("...")`` or ``os.path.join`` is not seen."""
    found = census(REPO)
    entries = [*run.IGNORED_READERS, *NOT_READERS]
    assert [module for module in found
            if not any(_covers(entry, module) for entry in entries)] == []
    assert [entry for entry in entries
            if not any(_covers(entry, module) for module in found)] == []


def test_the_census_sees_each_shape_it_names(tmp_path):
    """The control: a module calling ``load_dotenv`` (by attribute or by name), and one
    joining a path to an ignored name, are each found; one naming it otherwise is not."""
    names = ["tests/.fixture-profile", ".env.dev"]
    for text, listed in (("import dotenv\ndotenv.load_dotenv()\n", True),
                         ("from dotenv import dotenv_values\nvalues = dotenv_values()\n", True),
                         ("found = ROOT / '.env.dev'\n", True),
                         ("found = HERE / '.fixture-profile' / 'main.csv'\n", True),
                         ("found = ROOT / '.env.example'\n", False),
                         ("said = '.env.dev is ignored'\n", False)):
        module = tmp_path / "module.py"
        module.write_text(text, encoding="utf-8")
        assert _reads_ignored(module, names) is listed, text
