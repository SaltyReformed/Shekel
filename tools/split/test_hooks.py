"""The commit-time hooks that lint and test ``tools/split`` run on an edit to any module it
loads from another ``tools`` package (the branch's neutral review of the split-fix leaf, L8).
``pylint tools/split/`` reads what each of its imports binds, so a commit editing only
``tools/ci/archive.py`` must run the floor as it runs the tests.  CI step 4b runs both in
every scope either way: what a pattern that misses a module loses is the commit-time signal
alone."""
from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
import yaml

from tools.ci.arcs import REPO

#: The hooks that hold this package: its pylint floor and its tests.
HOOKS = ("pylint-split", "split-tests")

#: Run in a fresh interpreter with the repository root first on the path and this package's
#: module names after it: import each, then print the file of every module loaded.
_LOADED = ("import importlib, json, sys\n"
           "sys.path.insert(0, sys.argv[1])\n"
           "for name in sys.argv[2:]:\n"
           "    importlib.import_module(name)\n"
           "print(json.dumps([getattr(module, '__file__', None)\n"
           "                  for module in list(sys.modules.values())]))\n")


def _hook(hook_id: str) -> dict:
    """The hook ``hook_id`` as pre-commit reads it from the repository's config."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == hook_id:
                return hook
    raise AssertionError(f"no hook with id {hook_id!r} in .pre-commit-config.yaml")


@functools.cache
def _loaded_from_other_packages() -> tuple[str, ...]:
    """Every module of another ``tools`` package that importing each module of this one
    loads, transitively, as repository-relative paths.

    Read off ``sys.modules`` in a fresh interpreter, so the import system answers rather than
    a census of import statements, and nothing this test process already holds is counted.
    ``tools/__init__.py``, the tree's own docstring, is no package's module and no tools
    hook watches it, so it is not counted."""
    names = sorted(f"tools.split.{path.stem}" for path in (REPO / "tools/split").glob("*.py"))
    proc = subprocess.run([sys.executable, "-c", _LOADED, str(REPO), *names],
                          capture_output=True, text=True, check=True)
    root = REPO.resolve()
    found = set()
    for file in json.loads(proc.stdout):
        path = Path(file).resolve() if file else None
        if path is None or not path.is_relative_to(root):
            continue
        parts = PurePosixPath(path.relative_to(root)).parts
        if len(parts) > 2 and parts[0] == "tools" and parts[1] != "split":
            found.add("/".join(parts))
    return tuple(sorted(found))


def test_the_census_finds_what_the_package_loads():
    """A census that found nothing would pass the case below by grading none."""
    assert {"tools/ci/archive.py", "tools/ci/gitcmd.py"} <= set(_loaded_from_other_packages())


@pytest.mark.parametrize("hook_id", HOOKS)
def test_every_module_the_package_loads_runs_its_hooks(hook_id):
    """Editing a module this package loads runs its floor and its tests; and the hook runs
    this package, so a match is worth something."""
    hook = _hook(hook_id)
    assert any(arg.rstrip("/") == "tools/split" for arg in hook["args"])
    missed = [m for m in _loaded_from_other_packages() if not re.search(hook["files"], m)]
    assert not missed, f"{hook_id}'s files pattern does not match {missed}"
