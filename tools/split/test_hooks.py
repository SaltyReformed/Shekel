"""The commit-time hooks that lint and test ``tools/split`` run on an edit to any module it
loads from another ``tools`` package (the branch's neutral review of the split-fix leaf, L8).
``pylint tools/split/`` reads what each of its imports binds, so a commit editing only
``tools/ci/archive.py`` must run the floor as it runs the tests; and it reads its
configuration and loads its plugins, so an edit to either must run the floor too (the
coordinator's ruling on the delta review's nit 1).  CI step 4b runs both in every scope
either way: what a pattern that misses a module loses is the commit-time signal alone."""
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

#: Run in a fresh interpreter in the repository root: lint one module of this package with
#: the arguments after it (pylint finds its configuration and loads its plugins alike
#: whatever it lints), then print the configuration files pylint finds and the file of every
#: module loaded, its plugins among them.
_PYLINT_READS = ("import contextlib, io, json, sys\n"
                 "from pylint.config.find_default_config_files import "
                 "find_default_config_files\n"
                 "from pylint.lint import Run\n"
                 "with contextlib.redirect_stdout(io.StringIO()):\n"
                 "    Run(sys.argv[1:], exit=False)\n"
                 "print(json.dumps([str(path) for path in find_default_config_files()]\n"
                 "                 + [getattr(module, '__file__', None)\n"
                 "                    for module in list(sys.modules.values())]))\n")


def _repository_files(files: list[str | None]) -> set[PurePosixPath]:
    """Each of ``files`` that lies in the repository, repository-relative."""
    root = REPO.resolve()
    found = set()
    for file in files:
        path = Path(file).resolve() if file else None
        if path is not None and path.is_relative_to(root):
            found.add(PurePosixPath(path.relative_to(root)))
    return found


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
    return tuple(sorted(str(path) for path in _repository_files(json.loads(proc.stdout))
                        if len(path.parts) > 2 and path.parts[0] == "tools"
                        and path.parts[1] != "split"))


@functools.cache
def _read_by_pylint() -> tuple[str, ...]:
    """Every file of the repository outside this package that pylint reads besides the code
    it lints: the configuration it finds and the plugins it loads, as repository-relative
    paths.

    Read off pylint itself in a fresh interpreter in the repository root, so its own
    configuration discovery and its ``load-plugins`` answer rather than a list kept here."""
    proc = subprocess.run([sys.executable, "-c", _PYLINT_READS, "--score=n",
                           "tools/split/__init__.py"], cwd=REPO, capture_output=True, text=True,
                          check=True)
    return tuple(sorted(str(path) for path in _repository_files(json.loads(proc.stdout))
                        if path.parts[:2] != ("tools", "split")))


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


def test_the_census_finds_what_pylint_reads():
    """A census that found nothing would pass the case below by grading none."""
    assert {".pylintrc", "tools/pylint/shekel_checkers/__init__.py"} <= set(_read_by_pylint())


def test_every_file_pylint_reads_runs_the_floor():
    """Editing the configuration pylint reads, or a plugin it loads, runs this package's
    pylint floor: either changes pylint's verdict on ``tools/split`` with no file of it
    edited.  ``files`` is the hook's whole filter, because a ``types`` filter would drop
    ``.pylintrc``, which is not a Python file."""
    hook = _hook("pylint-split")
    assert not {"types", "types_or", "exclude", "exclude_types"} & set(hook)
    missed = [path for path in _read_by_pylint() if not re.search(hook["files"], path)]
    assert not missed, f"pylint-split's files pattern does not match {missed}"
