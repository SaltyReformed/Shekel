"""The processes the proof runs: the suite through ``./scripts/test.sh`` (never bare
pytest: the wrapper gives each run a database cluster of its own), pylint, the plan gate,
and ``git grep`` for the old module's name.  Each returns what it observed; judging it is
:mod:`tools.split._dynamic`'s.  It also reads what a record is judged in: how the working
tree differs from ``HEAD`` (:func:`differences`) and what the observations are made with
(:func:`environment`).

A run stopped by an exception does not outlive this process
(:func:`tools.testrecord.record.wrapper_run`).  The coordinator's condition (c) on 3B-2's
signal fork rested on :func:`subprocess.run` killing its child on any exception; it sends
``SIGKILL`` only to its DIRECT child, the wrapper, whose trap cannot answer it, so the
pytest the wrapper started and its database cluster outlived the tool, and the pytest wrote
its observation after the tool had stopped (measured 2026-10-15).
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import stat
import subprocess
import sys
from pathlib import Path

from tools.ci import gitcmd
from tools.ci.arcs import WRAPPER
from tools.split._model import Plan, mentions
from tools.split._observe import OBSERVE_ENV, setup_key, setups_path
from tools.split._source import decode, lines_of, load_json, read_json, undecodable
from tools.testrecord.record import shown, wrapper_env, wrapper_run, wrapper_tail

#: pylint's exit-status bits for a fatal message and a usage error: either means it did
#: not lint, so its message list is not an observation.
_PYLINT_BROKEN = 1 | 32

#: The linter ``record`` starts (the suite wrapper is :data:`tools.ci.arcs.WRAPPER`).
PYLINT = "pylint"

#: A regular file's mode in git's tree, by whether its owner may execute it: the only
#: entries :func:`differences` models.
_FILE_MODES = {False: "100644", True: "100755"}

#: Each module a run executes that reads a file the COMMITTED ignore rules hide, so that it
#: is no difference from ``HEAD`` and yet changes what an observation means: the file's
#: name, and whether the module looks for it in its own directory and each one above it
#: (True) or at the repository root alone.  ``app/config.py`` calls ``load_dotenv()`` on
#: import, which reads the first ``.env`` found from its directory up to the filesystem's
#: root (python-dotenv's ``find_dotenv``); ``test_env_dev_file_does_not_exist`` asserts the
#: root holds no ``.env.dev``.  ``tools/split/test_run.py`` fails when a census of the
#: tracked tree finds a reader this lacks (the coordinator's ruling on delta review 3's
#: M-2, 2026-10-16).
IGNORED_READERS = {
    "app/config.py": (".env", True),
    "tests/test_deploy/test_docker_secrets_and_env_hygiene.py": (".env.dev", False),
}


class RunError(RuntimeError):
    """A run did not produce the observation it exists to produce."""


def observe(root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
    """Run ``targets`` through the suite wrapper with the observer loaded and return its
    observation (collect-only runs single-process, so the collection is the run's own).
    The fixture setups every process wrote beside it are left for the one reader that
    judges them (:func:`merged_setups`, called by ``split._record_verdict``).

    ``out`` lies in a directory the calling command made for its run
    (``split._run_directory``), so no file there is older than the run, and a pytest that
    outlived an earlier run writes into that run's directory, never this one's (the
    coordinator's rulings on 3B-2b's condition (c), 2026-10-15, and on 3C-2's prove fork,
    2026-10-16).  Nothing is deleted before the run: a file deleted first could still be
    written by such a pytest after the deletion.  The wrapper starts in the environment
    every wrapper run the tools start shares (:func:`tools.testrecord.record.wrapper_env`),
    so it hashes with the recorded runs' seed and inherits no xdist identity."""
    env = wrapper_env(root)
    env[OBSERVE_ENV] = str(out)
    args = [WRAPPER, *targets, "-p", "tools.split._observe",
            "-p", "no:cacheprovider", "-q"]
    if collect_only:
        args += ["-n", "0", "--collect-only"]
    returncode, output = wrapper_run(args, root, env)
    if not out.exists():
        raise RunError(f"{' '.join(args)} wrote no observation (exit {returncode}):\n"
                       + wrapper_tail(output))
    return read_json(out)


def merged_setups(out: Path) -> tuple[list[dict], list[str]]:
    """Every process's fixture-setup file beside the observation ``out``, merged: each
    fixture set up, once, and every test some process recorded running."""
    setups: dict[tuple, dict] = {}
    ran: set[str] = set()
    for path in _setups_files(out):
        held = read_json(path)
        for setup in held["setups"]:
            setups.setdefault(setup_key(setup), setup)
        ran.update(held["ran"])
    return list(setups.values()), sorted(ran)


def _setups_files(out: Path) -> list[Path]:
    """Every process's fixture-setup file beside the observation ``out``
    (:func:`tools.split._observe.setups_path`)."""
    return sorted(out.parent.glob(setups_path(out, "*").name))


def pylint_messages(root: Path, paths: list[str]) -> list[dict]:
    """pylint's messages for ``paths``, linted in ONE run so duplicate-code between them
    is seen."""
    proc = subprocess.run([PYLINT, "--output-format=json", "--score=n", *paths], cwd=root,
                          capture_output=True, check=False)
    if proc.returncode & _PYLINT_BROKEN:
        raise RunError(f"pylint did not lint {paths} (exit {proc.returncode}): "
                       f"{shown(proc.stderr).strip()[-500:]}")
    return load_json(decode(proc.stdout, "pylint's output") or "[]", "pylint's output")


def differences(root: Path) -> set[str]:
    """Every path, repository-relative, at which the working tree is not ``HEAD``'s.  The
    ONE producer of the tree's state a record is judged in (:mod:`tools.split.split`):
    ``record`` and ``apply`` refuse unless it is empty, and ``prove`` unless it is exactly
    what ``apply`` writes.

    It reads the files themselves, never ``git status``'s view of them (the coordinator's
    ruling on delta review 3's H-1, the twelfth unit correction, 2026-10-16): status judges a
    tracked file through the index, so an edit to a file flagged ``--skip-worktree`` or
    ``--assume-unchanged``, or to a path a sparse checkout leaves out, was no difference to
    it, and ``.git/info/exclude`` and ``core.excludesFile``, rules kept per machine, hid an
    untracked file.  A path is listed when it is:

    - tracked by ``HEAD`` (``git ls-tree -r HEAD``) and on disk missing, not a regular
      file, of another mode (executable by its owner or not, whatever ``core.fileMode``
      says), or holding other bytes: git's own hash of them (``git hash-object
      --no-filters``, which writes no object) is not the blob's;
    - held by the index and not by ``HEAD``, and on disk (a file staged);
    - untracked, and covered by no ignore rule the ``.gitignore`` files hold
      (``git ls-files --others --exclude-per-directory=.gitignore``, which reads neither
      per-machine source);
    - an untracked ``.gitignore`` git reads, that is, one in a directory no rule ignores
      (``--others --ignored --directory`` lists it, and never descends into an ignored
      directory).  ``--exclude-per-directory`` reads the ``.gitignore`` files ON DISK, so
      an untracked one would hide whatever it names, itself included (the thirteenth unit
      correction, ruled 2026-10-16); its rules are not ``HEAD``'s, so it is listed itself,
      and the files only it hides are listed once it is gone.

    A path ``HEAD`` holds as a symlink or a submodule is refused, by name, as not modelled:
    git hashes a symlink's target, and a submodule's files are another repository's (the
    coordinator's condition on the thirteenth correction).  Every call is
    ``--no-optional-locks`` and none refreshes the index, so asking writes nothing."""
    tracked = _head_files(root)
    unmodelled = sorted(path for path, (mode, _) in tracked.items()
                        if mode not in _FILE_MODES.values())
    if unmodelled:
        raise RunError(f"HEAD holds {len(unmodelled)} path(s) as a symlink or a submodule, "
                       f"e.g. {unmodelled[:5]}: not modelled; this tool judges a tree of "
                       "regular files only")
    found, regular = set(), []
    for path, (mode, _) in tracked.items():
        try:
            held = os.lstat(root / path)
        except (FileNotFoundError, NotADirectoryError):
            found.add(path)
            continue
        if not stat.S_ISREG(held.st_mode) or (
                _FILE_MODES[bool(held.st_mode & stat.S_IXUSR)] != mode):
            found.add(path)
        else:
            regular.append(path)
    found.update(path for path, blob in zip(regular, _hashes(root, regular))
                 if blob != tracked[path][1])
    found.update(path for path in _listed(root, "--cached")
                 if path not in tracked and os.path.lexists(root / path))
    found.update(_listed(root, "--others", "--exclude-per-directory=.gitignore"))
    found.update(path for path in _listed(root, "--others", "--ignored", "--directory",
                                          "--exclude-per-directory=.gitignore")
                 if path.rsplit("/", 1)[-1] == ".gitignore")
    return found


def _head_files(root: Path) -> dict[str, tuple[str, str]]:
    """Every path ``HEAD`` tracks, repository-relative: its mode and its object's name."""
    proc = _git(root, "git ls-tree's output", b"\0", "--no-optional-locks", "ls-tree", "-r",
                "-z", "--full-tree", "HEAD")
    if proc.returncode:
        raise RunError(f"git ls-tree failed: {proc.stderr.strip()}")
    tracked = {}
    for entry in proc.stdout.split("\0"):
        if entry:
            held, path = entry.split("\t", 1)
            mode, _kind, name = held.split(" ")
            tracked[path] = (mode, name)
    return tracked


def _hashes(root: Path, paths: list[str]) -> list[str]:
    """git's object name for the bytes of each file of ``paths``, in order, as a blob, with
    no filter applied: the bytes on disk, judged as git names a blob."""
    if not paths:
        return []
    proc = _git(root, "git hash-object's output", b"\n", "--no-optional-locks", "hash-object",
                "--no-filters", "--", *paths)
    if proc.returncode:
        raise RunError(f"git hash-object failed: {proc.stderr.strip()}")
    return proc.stdout.split()


def _listed(root: Path, *args: str) -> list[str]:
    """The paths ``git ls-files -z <args>`` lists."""
    proc = _git(root, "git ls-files' output", b"\0", "--no-optional-locks", "ls-files", "-z",
                *args)
    if proc.returncode:
        raise RunError(f"git ls-files failed: {proc.stderr.strip()}")
    return [path for path in proc.stdout.split("\0") if path]


def sha256(path: Path) -> str:
    """A file's sha256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ignored_reads(root: Path) -> list[Path]:
    """Every path at which a run reads a file the committed ignore rules hide, so that no
    check of the tree sees it (:data:`IGNORED_READERS`), in the order its reader looks."""
    found = []
    for reader, (name, upward) in IGNORED_READERS.items():
        start = (root / reader).parent
        found += [where / name for where in (start, *start.parents)] if upward else [
            root / name]
    return found


def _state(path: Path) -> str:
    """What a run reading ``path`` finds there: its bytes' sha256, or that there is none."""
    if path.is_file():
        return sha256(path)
    return "present, not a regular file" if path.exists() else "absent"


def environment(root: Path) -> str:
    """The sha256 naming what this process makes a record's observations with: the
    interpreter's version (``sys.version``), every distribution installed where it imports
    from (``name==version``, sorted, so the order they are found in is no difference),
    ``git --version`` (the census and the importer walk are git's answers), and each file a
    run reads that the committed ignore rules hide (:func:`ignored_reads`), its bytes or its
    absence (the coordinator's ruling on delta review 3's M-2, 2026-10-16: neither the tree
    nor the rest of this held ``.env``, which ``app/config.py`` reads on every import).
    ``record`` keeps it, and ``apply`` and ``prove`` refuse a record whose environment is
    not this one (the coordinator's ruling on delta review 2, REVISED (B) (2), 2026-10-15).

    It is THIS interpreter's: the tool's.  The suite wrapper starts the ``pytest`` on
    ``PATH`` and pylint is the ``pylint`` on ``PATH``, which are this interpreter's own
    when the tool runs from the virtualenv that holds them, and are not covered when they
    belong to another installation.  Nor are the environment variables a run reads
    (``PYTEST_ADDOPTS``, the wrapper's ``PYTEST_MARKER_EXPR``)."""
    proc = _git(root, "git --version's output", b"\n", "--version")
    if proc.returncode:
        raise RunError(f"git --version failed: {proc.stderr.strip()}")
    held = [sys.version, proc.stdout.strip(), *sorted(
        f"{dist.name}=={dist.version}" for dist in importlib.metadata.distributions()),
            *(f"{path}: {_state(path)}" for path in ignored_reads(root))]
    return hashlib.sha256("\n".join(held).encode("utf-8")).hexdigest()


def plan_gate(root: Path) -> tuple[int, str]:
    """The plan gate's exit status and summary line, run as pre-commit and CI run it."""
    proc = subprocess.run(["pytest", "tools/plan_gate", "-c", "/dev/null", "-q"], cwd=root,
                          capture_output=True, check=False)
    return proc.returncode, (lines_of(decode(proc.stdout, "the plan gate's output").strip())
                             or [""])[-1]


def _git(root: Path, what: str, separator: bytes, *args: str
         ) -> subprocess.CompletedProcess[str]:
    """``git <args>`` in ``root`` through :func:`tools.ci.gitcmd.run`, which reads its output
    as text; refused, naming ``what`` and showing the record that holds the byte (one record
    of the output ends at ``separator``), when that output is not UTF-8."""
    try:
        return gitcmd.run(root, *args)
    except UnicodeDecodeError as exc:
        raise undecodable(exc, what, separator) from exc


def tree_files(root: Path, *pathspecs: str) -> list[Path]:
    """Every file of the working tree that git does not ignore, tracked or not, and present
    on disk, matching ``pathspecs`` (every such file when none is given): the ONE walk that
    both the importer walk (:func:`python_files`) and the census (:func:`census`) read.

    Exactly ``git ls-files --cached --others --exclude-standard``: every file the index
    tracks, whether an ignore rule covers it or not, and every untracked file no ignore rule
    (``.gitignore``, ``.git/info/exclude``, ``core.excludesFile``) covers; then only those
    present on disk as files.  git ignores only what it does not track, so a file tracked
    under a directory that ``.gitignore`` names is listed.  The census once read the tree
    through ``git grep --untracked``, a second spelling of this set, and that spelling skips
    such a file (measured on git 2.56, 2026-10-15, in a repository bound to nothing; no
    file of this repository was tracked under an ignored path that day), so the two walks
    disagreed.  It starts git through :func:`tools.ci.gitcmd.run`, so ``root`` alone is the
    repository read, and no ``GIT_*`` variable the caller exported (a pathspec's magic, a
    config) changes the set."""
    proc = _git(root, "git ls-files' output", b"\0", "ls-files", "-z", "--cached", "--others",
                "--exclude-standard", "--", *pathspecs)
    if proc.returncode:
        raise RunError(f"git ls-files failed: {proc.stderr.strip()}")
    return sorted({root / path for path in proc.stdout.split("\0")
                   if path and (root / path).is_file()})


def python_files(root: Path) -> list[Path]:
    """Every ``.py`` file of :func:`tree_files`: where a module importing the split source may
    live.

    The scope is the whole tree, as the census's is (the coordinator's ruling on the
    follow-up leaf's review fork L7, 2026-10-15): four top-level directories were the
    census's directory-list error again, and the prover reused the writer's walk, so it could
    not see what the writer missed."""
    return tree_files(root, "*.py")


def census(root: Path, plan: Plan) -> list[tuple[str, int, str]]:
    """Every line naming the old module (:func:`mentions`), as ``(path, line, text)`` in
    path and line order, in every file :func:`tree_files` lists.

    The scope is every line of this working tree (the coordinator's ruling on the branch's
    neutral review, F1 and F2, 2026-10-15).  ``prove`` runs before the new package is
    tracked, so a census of tracked files never read the package's own lines; and one held
    to six top-level directories missed ``README.md`` and a migration's docstring.  The files
    are the importer walk's, so the two cannot disagree about what the tree holds; ``git grep
    --no-index`` reads exactly the files named (``--literal-pathspecs``: a ``[`` or ``*`` in
    a path is that character), and none when none is named, because with no path it would
    read every file under ``root``, ignored ones too.  ``-I`` skips binary files, which hold
    no line to edit.  ``-z`` separates each path and line number with a NUL, so a path is
    never quoted and a colon in it is never read as a separator, and ``--no-column`` keeps a
    ``grep.column`` setting, which ``--no-index`` still reads, out of the record.  git's
    output is read as text, decoded strictly, so a carriage return inside a matched line
    stops the census with an error rather than a misread line, and a matched line not in
    UTF-8 is refused, the refusal showing its record, path first (:func:`_git`); a carriage
    return that ends a line (CRLF) is read as its end (no tracked file holds a carriage
    return or text not in UTF-8, measured 2026-10-15).

    git finds every line holding the name as a FIXED string, so the one judge of what a
    mention is stays :func:`mentions`.  The walk is the one call that reads a repository,
    and it starts through :func:`tools.ci.gitcmd.run`, so ``root`` alone is the repository
    read, whatever a hook has bound this process to; ``--no-index`` reads none, and starts
    there too."""
    files = [str(path.relative_to(root)) for path in tree_files(root)]
    if not files:
        return []
    try:
        proc = _git(root, "git grep's output", b"\n", "--literal-pathspecs", "grep",
                    "--no-index", "-n", "--no-column", "-z", "-I", "-F", plan.stem, "--", *files)
    except OSError as exc:
        raise RunError(f"git grep could not start over the tree's {len(files)} files "
                       f"({exc}); the census read nothing") from exc
    if proc.returncode not in (0, 1):
        raise RunError(f"git grep failed: {proc.stderr.strip()}")
    mention = mentions(plan)
    found = []
    for record in proc.stdout.split("\n")[:-1]:
        try:
            path, line, text = record.split("\0", 2)
        except ValueError as exc:
            raise RunError(f"git grep's record {record[:80]!r} is not path, line and text: "
                           "a carriage return in a matched line ends a record early") from exc
        if mention.search(text):
            found.append((path, int(line), text))
    return sorted(found)
