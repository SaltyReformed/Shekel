"""The processes the proof runs: the suite through ``./scripts/test.sh`` (never bare
pytest: the wrapper gives each run a database cluster of its own), pylint, the plan gate,
and ``git grep`` for the old path.  Each returns what it observed; judging it is
:mod:`tools.split._dynamic`'s.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.split._model import Plan
from tools.split._observe import OBSERVE_ENV

#: pylint's exit-status bits for a fatal message and a usage error: either means it did
#: not lint, so its message list is not an observation.
_PYLINT_BROKEN = 1 | 32

#: Where the census looks for the old path.
CENSUS_PATHSPECS = ("tests", "tools", "scripts", "docs", ".claude", "app")


class RunError(RuntimeError):
    """A run did not produce the observation it exists to produce."""


def observe(root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
    """Run ``targets`` through the suite wrapper with the observer loaded and return its
    observation (collect-only runs single-process, so the collection is the run's own)."""
    out.unlink(missing_ok=True)
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
    return observed


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


def census_pattern(plan: Plan) -> str:
    """The extended regex the census greps: the source's file name (with or without its
    directories) and its dotted module path, but not a path INTO the new package."""
    file_name = re.escape(plan.source.name)
    dotted = re.escape(plan.dotted)
    return rf"(^|[^A-Za-z0-9_]){file_name}|{dotted}($|[^A-Za-z0-9_.])"


def census(root: Path, plan: Plan) -> list[str]:
    """``git grep -n`` lines naming the old path, in tracked files under the census roots."""
    proc = subprocess.run(["git", "grep", "-n", "-E", census_pattern(plan), "--",
                           *CENSUS_PATHSPECS], cwd=root, capture_output=True, text=True,
                          check=False)
    if proc.returncode not in (0, 1):
        raise RunError(f"git grep failed: {proc.stderr.strip()}")
    return proc.stdout.splitlines()
