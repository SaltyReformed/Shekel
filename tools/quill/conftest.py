"""The fixture the ``quill`` command's tests share: a throwaway code repository."""
from __future__ import annotations

import pytest

from tools.ci.scratch import point_dev
from tools.ci.scratch import run as _run
from tools.quill import _git, _migrate_fake, _migrate_source


@pytest.fixture(name="code")
def _code(tmp_path, monkeypatch):
    """A code repository under tmp_path with an ``origin/dev``; fetching is a no-op."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=feat/work")
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    base = _run(root, "commit-tree", tree, "-m", "base")
    _run(root, "update-ref", "refs/heads/feat/work", base)
    point_dev(root, base)
    monkeypatch.setattr(_git, "fetch", lambda _root: None)
    monkeypatch.setattr(_git, "pushed", lambda _root, branch: branch == "feat/pushed")
    monkeypatch.setattr(_git, "origin_repository", lambda _root: "o/code")
    return root


@pytest.fixture(name="corpus")
def _corpus(monkeypatch):
    """The migration's small plan (:mod:`_migrate_fake`), R-BAL240's deploy-together key
    pointed at its container and no card filed already, since the live keys name steps
    the corpus does not hold.  ``MAPPED`` is changed in place: every module reads the one
    dict."""
    monkeypatch.setattr(_migrate_source, "DEPLOY_TOGETHER", frozenset({"balance:A-2"}))
    for key in list(_migrate_source.MAPPED):
        monkeypatch.delitem(_migrate_source.MAPPED, key)
    return _migrate_fake.registries()
