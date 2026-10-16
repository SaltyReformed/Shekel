"""How :mod:`tools.ci.gitcmd` starts git: with no ``GIT_*`` variable of the caller's but
:data:`~tools.ci.gitcmd.PASS_THROUGH` (the coordinator's rulings on the split tool's second
delta review, Medium-2, and on 3C-3's third fork, 2026-10-16).

What that rule is FOR is graded where the answers are read: no variable a caller exports
changes the split tool's importer walk or census (``tools/split/test_dynamic.py``), no
repository a caller is bound to is reached (each caller's bound-sentinel case:
``tools/ci/test_trailers.py``, ``tools/quill/test__git.py``,
``tools/split/test_dynamic.py``), and the pass-through still dates quill's scratch commits
(``tools/quill/test__git.py``).  This module holds the premise the rule rests on.
"""
from __future__ import annotations

from tools.ci.gitcmd import PASS_THROUGH, git


def test_every_variable_git_names_as_binding_a_repository_starts_with_git(tmp_path):
    """``git rev-parse --local-env-vars`` names the variables that bind a git process to one
    repository (the list git clears before entering a submodule), which
    :func:`tools.ci.gitcmd.run` once removed by name.  The prefix rule removes them all only
    while every one starts with ``GIT_`` and none is passed through.  This grades git, not
    the module: it fails the day a git names a binding variable the prefix would leave in
    place."""
    names = git(tmp_path, "rev-parse", "--local-env-vars").split()
    assert {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"} <= set(names)
    assert [name for name in names
            if not name.startswith("GIT_") or name in PASS_THROUGH] == []
