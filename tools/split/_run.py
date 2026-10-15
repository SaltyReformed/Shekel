"""The processes the proof runs: the suite through ``./scripts/test.sh`` (never bare
pytest: the wrapper gives each run a database cluster of its own), pylint, the plan gate,
and ``git grep`` for the old module's name.  Each returns what it observed; judging it is
:mod:`tools.split._dynamic`'s.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.ci import gitcmd
from tools.split._model import RESERVED, Plan
from tools.split._observe import OBSERVE_ENV, setups_path

#: pylint's exit-status bits for a fatal message and a usage error: either means it did
#: not lint, so its message list is not an observation.
_PYLINT_BROKEN = 1 | 32


class RunError(RuntimeError):
    """A run did not produce the observation it exists to produce."""


def observe(root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
    """Run ``targets`` through the suite wrapper with the observer loaded and return its
    observation (collect-only runs single-process, so the collection is the run's own),
    with every process's fixture setups merged in: ``setups``, each fixture once, and
    ``setups_ran``, every test some process recorded running."""
    out.unlink(missing_ok=True)
    for stale in _setups_files(out):
        stale.unlink()
    env = dict(os.environ)
    env[OBSERVE_ENV] = str(out)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(root), env.get("PYTHONPATH")) if p)
    args = ["./scripts/test.sh", *targets, "-p", "tools.split._observe",
            "-p", "no:cacheprovider", "-q"]
    if collect_only:
        args += ["-n", "0", "--collect-only"]
    proc = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True,
                          check=False)
    if not out.exists():
        raise RunError(f"{' '.join(args)} wrote no observation (exit {proc.returncode}):\n"
                       + "\n".join((proc.stdout + proc.stderr).splitlines()[-30:]))
    observed = json.loads(out.read_text(encoding="utf-8"))
    observed["returncode"] = proc.returncode
    observed["summary"] = (proc.stdout.strip().splitlines() or [""])[-1]
    observed["setups"], observed["setups_ran"] = merged_setups(out)
    return observed


def merged_setups(out: Path) -> tuple[list[dict], list[str]]:
    """Every process's fixture-setup file beside the observation ``out``, merged: each
    fixture set up, once, and every test some process recorded running."""
    setups: dict[tuple, dict] = {}
    ran: set[str] = set()
    for path in _setups_files(out):
        held = json.loads(path.read_text(encoding="utf-8"))
        for setup in held["setups"]:
            setups.setdefault((setup["argname"], setup["scope"], setup["baseid"],
                               setup["file"]), setup)
        ran.update(held["ran"])
    return list(setups.values()), sorted(ran)


def _setups_files(out: Path) -> list[Path]:
    """Every process's fixture-setup file beside the observation ``out``
    (:func:`tools.split._observe.setups_path`)."""
    return sorted(out.parent.glob(setups_path(out, "*").name))


def pylint_messages(root: Path, paths: list[str]) -> list[dict]:
    """pylint's messages for ``paths``, linted in ONE run so duplicate-code between them
    is seen."""
    proc = subprocess.run(["pylint", "--output-format=json", "--score=n", *paths], cwd=root,
                          capture_output=True, text=True, check=False)
    if proc.returncode & _PYLINT_BROKEN:
        raise RunError(f"pylint did not lint {paths} (exit {proc.returncode}): "
                       f"{proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout or "[]")


def plan_gate(root: Path) -> tuple[int, str]:
    """The plan gate's exit status and summary line, run as pre-commit and CI run it."""
    proc = subprocess.run(["pytest", "tools/plan_gate", "-c", "/dev/null", "-q"], cwd=root,
                          capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


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
    """Every line naming the old module (:func:`mentions`), as ``(path, line, text)``, in
    every file of the working tree that git does not ignore, tracked or not.

    The scope is the property, a line anywhere a reader could follow (the coordinator's
    ruling on the branch's neutral review, F1 and F2, 2026-10-15).  ``prove`` runs before the
    new package is tracked, so a census of tracked files never read the package's own lines;
    and one held to six top-level directories missed ``README.md`` and a migration's
    docstring.  ``--untracked`` reads untracked files too and honours ``.gitignore``; ``-I``
    skips binary files, which hold no line to edit.  ``-z`` separates each path and line
    number with a NUL, so a path is never quoted and a colon in it is never read as a
    separator; records are split on newlines only, the one character git ends a line at.

    git finds every line holding the name as a FIXED string, so the one judge of what a
    mention is stays :func:`mentions`.  It starts through :func:`tools.ci.gitcmd.run`, so
    ``root`` alone is the repository read, whatever a hook has bound this process to."""
    proc = gitcmd.run(root, "grep", "-n", "-z", "-I", "-F", "--untracked", plan.stem)
    if proc.returncode not in (0, 1):
        raise RunError(f"git grep failed: {proc.stderr.strip()}")
    mention = mentions(plan)
    found = []
    for record in proc.stdout.split("\n")[:-1]:
        path, line, text = record.split("\0", 2)
        if mention.search(text):
            found.append((path, int(line), text))
    return found
