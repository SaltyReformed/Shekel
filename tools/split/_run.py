"""The processes the proof runs: the suite through ``./scripts/test.sh`` (never bare
pytest: the wrapper gives each run a database cluster of its own), pylint, the plan gate,
and ``git grep`` for the old module's name.  Each returns what it observed; judging it is
:mod:`tools.split._dynamic`'s.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tools.ci import gitcmd
from tools.split._model import RESERVED, Plan
from tools.split._observe import OBSERVE_ENV, setup_key, setups_path
from tools.split._source import decode, load_json, read_json, shown, undecodable

#: pylint's exit-status bits for a fatal message and a usage error: either means it did
#: not lint, so its message list is not an observation.
_PYLINT_BROKEN = 1 | 32


class RunError(RuntimeError):
    """A run did not produce the observation it exists to produce."""


def observe(root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
    """Run ``targets`` through the suite wrapper with the observer loaded and return its
    observation (collect-only runs single-process, so the collection is the run's own).
    The fixture setups every process wrote beside it are left for the one reader that
    judges them (:func:`merged_setups`, called by ``split._record_verdict``)."""
    discard(out)
    env = dict(os.environ)
    env[OBSERVE_ENV] = str(out)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(root), env.get("PYTHONPATH")) if p)
    args = ["./scripts/test.sh", *targets, "-p", "tools.split._observe",
            "-p", "no:cacheprovider", "-q"]
    if collect_only:
        args += ["-n", "0", "--collect-only"]
    proc = subprocess.run(args, cwd=root, env=env, capture_output=True, check=False)
    if not out.exists():
        raise RunError(f"{' '.join(args)} wrote no observation (exit {proc.returncode}):\n"
                       + "\n".join(shown(proc.stdout + proc.stderr).splitlines()[-30:]))
    observed = read_json(out)
    observed["returncode"] = proc.returncode
    observed["summary"] = (shown(proc.stdout).strip().splitlines() or [""])[-1]
    return observed


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


def discard(out: Path) -> None:
    """Delete the file ``out`` and, when it is an observation, every process's fixture-setup
    file beside it, so nothing a previous run wrote is read as this run's."""
    out.unlink(missing_ok=True)
    for stale in _setups_files(out):
        stale.unlink()


def _setups_files(out: Path) -> list[Path]:
    """Every process's fixture-setup file beside the observation ``out``
    (:func:`tools.split._observe.setups_path`)."""
    return sorted(out.parent.glob(setups_path(out, "*").name))


def pylint_messages(root: Path, paths: list[str]) -> list[dict]:
    """pylint's messages for ``paths``, linted in ONE run so duplicate-code between them
    is seen."""
    proc = subprocess.run(["pylint", "--output-format=json", "--score=n", *paths], cwd=root,
                          capture_output=True, check=False)
    if proc.returncode & _PYLINT_BROKEN:
        raise RunError(f"pylint did not lint {paths} (exit {proc.returncode}): "
                       f"{shown(proc.stderr).strip()[-500:]}")
    return load_json(decode(proc.stdout, "pylint's output") or "[]", "pylint's output")


def plan_gate(root: Path) -> tuple[int, str]:
    """The plan gate's exit status and summary line, run as pre-commit and CI run it."""
    proc = subprocess.run(["pytest", "tools/plan_gate", "-c", "/dev/null", "-q"], cwd=root,
                          capture_output=True, check=False)
    return proc.returncode, (decode(proc.stdout, "the plan gate's output").strip().splitlines()
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
    repository read."""
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


def mentions(plan: Plan) -> re.Pattern[str]:
    """A mention of the source module's NAME, in any spelling, that does not continue INTO
    the new package.

    The unit is the name, never a list of spellings (the coordinator's ruling on the split
    session's fork over the fourth split, 2026-10-15): ``m.py``, ``tests.x.m``, ``m.Member``,
    a module-level ``m.test_fn`` and a bare ``m`` all name the old module, and a census that
    matched only the first two missed a live ``m.Member`` pointer.  A mention is dropped only
    where it continues into the package: followed by ``/`` (its directory) or by ``.`` and
    one of the package's own module names, read from the plan.  Whatever this over-lists is
    the safe side, because each line is classified by the pointer rule."""
    own = "|".join(sorted(RESERVED | {part.module for part in plan.parts}))
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(plan.stem)}(?![A-Za-z0-9_])"
                      rf"(?!/|\.(?:{own})(?![A-Za-z0-9_]))")


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
