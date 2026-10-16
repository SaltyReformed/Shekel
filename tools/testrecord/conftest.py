"""The recorder's tests' one sample project: a tree with its own ``app/``, ``tests/`` and
``pytest.ini``, and a STAND-IN for ``scripts/test.sh`` that runs pytest itself (the tool
suite starts no docker; the real wrapper's contract under a signal is graded through
:func:`tools.split._run.observe`).  The recorder is imported from this repository, the
sample's own directories first on the path.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from tools.ci.arcs import REPO, WRAPPER

#: The sample's one app module: a branch each parametrized case takes one side of.
CALC = textwrap.dedent('''\
    def double(x):
        if x > 1:
            return x * 2
        return x + x
    ''')

#: The sample's test module, every outcome among its tests.
SAMPLE = textwrap.dedent('''\
    import pytest
    from app.calc import double


    @pytest.fixture
    def two():
        value = 2
        yield value
        assert value == 2


    @pytest.fixture
    def broken():
        raise RuntimeError("setup fails")


    @pytest.mark.parametrize("v", [1, 3])
    def test_branch(v):
        assert double(v) == 2 * v


    def test_long(two):
        assert "a" * 300 == "a" * 300
        assert two == 2


    def test_fails():
        assert double(1) == 3


    @pytest.mark.skip(reason="skipped")
    def test_skipped():
        pass


    @pytest.mark.xfail(strict=False)
    def test_xfail():
        assert double(1) == 3


    @pytest.mark.xfail(strict=False)
    def test_xpass():
        pass


    def test_error(broken):
        pass
    ''')

#: Every test of :data:`SAMPLE`, in its order, as the run names it.
SAMPLE_IDS = [f"tests/test_sample.py::{name}" for name in (
    "test_branch[1]", "test_branch[3]", "test_long", "test_fails", "test_skipped",
    "test_xfail", "test_xpass", "test_error")]


@pytest.fixture(name="project")
def _project(tmp_path: Path, monkeypatch) -> Path:
    """The sample project under ``tmp_path``, its wrapper a stand-in running this
    interpreter's pytest, and this repository after it on the path (for the plugin)."""
    root = tmp_path / "project"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app/__init__.py").write_text("", encoding="utf-8")
    (root / "app/calc.py").write_text(CALC, encoding="utf-8")
    (root / "tests/test_sample.py").write_text(SAMPLE, encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    wrapper = root / WRAPPER
    wrapper.parent.mkdir()
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pytest "$@"\n',
                       encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    return root


@pytest.fixture(name="out")
def _out(tmp_path: Path) -> Path:
    """An empty directory made for one run."""
    found = tmp_path / "run"
    found.mkdir()
    return found
