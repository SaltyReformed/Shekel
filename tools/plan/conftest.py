"""The fixture the ``plan`` command's tests share: a throwaway code repository."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run as _run
from tools.plan import _git


@pytest.fixture(name="code")
def _code(tmp_path, monkeypatch):
    """A code repository under tmp_path with an ``origin/dev``; fetching is a no-op."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=feat/work")
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    base = _run(root, "commit-tree", tree, "-m", "base")
    _run(root, "update-ref", "refs/heads/feat/work", base)
    _run(root, "update-ref", "refs/remotes/origin/dev", base)
    monkeypatch.setattr(_git, "fetch", lambda _root: None)
    monkeypatch.setattr(_git, "pushed", lambda _root, branch: branch == "feat/pushed")
    monkeypatch.setattr(_git, "origin_repository", lambda _root: "o/code")
    return root
