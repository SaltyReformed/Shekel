"""The fixture the split tool's tests share: a throwaway repository holding one sample test
module that exercises every shape the tool moves (an autouse fixture, banner comments, a
helper, constants, a parametrize list, whole classes, a module-level test, and a class split
across two parts whose tests pass, skip, xfail and xpass), a module it imports from, an
importer of one of its helpers, and a plan for it."""
from __future__ import annotations

import copy
import json
import textwrap
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split._layout import Layout, resolve
from tools.split._model import parse_plan
from tools.split._source import collect_rules, read_module

SOURCE = "tests/test_area/test_sample.py"

SAMPLE = textwrap.dedent('''\
    """Tests for the sample area.

    A second paragraph, kept verbatim in the package's __init__.py.
    """

    import re
    from datetime import date

    import pytest

    from tests.shared import helper_one, helper_two, unused_thing


    @pytest.fixture(autouse=True)
    def _frozen(monkeypatch):
        """Every test here runs with SAMPLE set."""
        monkeypatch.setenv("SAMPLE", "1")


    # -- Helpers ------------------------------------------------------

    # The day everything starts.
    _START = date(2026, 1, 1)


    def _make(n):
        """Make a thing."""
        return helper_one(n)


    _CASES = [("a", 1), ("b", 2)]


    # -- First section ------------------------------------------------
    class TestFirst:
        """First tests."""

        def test_one(self):
            """One."""
            assert _make(1) == 1

        @pytest.mark.parametrize("label,value", _CASES)
        def test_cases(self, label, value):
            """Cases."""
            assert re.match(r"\\w", label) and value > 0


    def test_top_level():
        """A module-level test."""
        assert _START.year == 2026


    class TestBig:
        """The big class, which is split across two parts."""

        def _shared(self):
            """A member every slice reaches through self."""
            return helper_two()

        def test_a(self):
            """A."""
            assert self._shared() == 2

        # a comment above b
        def test_b(self):
            """B."""
            assert True

        @pytest.mark.skip(reason="skipped on purpose")
        def test_c(self):
            """C."""

        @pytest.mark.xfail(reason="fails on purpose", strict=True)
        def test_d(self):
            """D."""
            assert False

        @pytest.mark.xfail(reason="passes anyway")
        def test_e(self):
            """E."""
            assert True
    ''')

SHARED = textwrap.dedent('''\
    """What the sample imports."""


    def helper_one(n):
        """Return n."""
        return n


    def helper_two():
        """Return 2."""
        return 2


    unused_thing = 0
    ''')

IMPORTER = textwrap.dedent('''\
    """A module that imports a helper of the sample."""
    from tests.test_area.test_sample import _make


    def test_uses_make():
        """It reaches the helper."""
        assert _make(3) == 3
    ''')

PLAN = {
    "source": SOURCE,
    "parts": [
        {"module": "test_first", "docstring": "The first section and the top-level test.",
         "nodes": ["TestFirst", "test_top_level"]},
        {"module": "test_big_early", "docstring": "TestBig's first two tests.",
         "nodes": [{"class": "TestBig", "tests": ["test_a", "test_b"],
                    "docstring": "test_a and test_b; members on _TestBigMembers."}]},
        {"module": "test_big_late", "docstring": "TestBig's last three tests.",
         "nodes": [{"class": "TestBig", "tests": ["test_c", "test_d", "test_e"],
                    "docstring": "test_c to test_e; members on _TestBigMembers."}]},
    ],
    "unused_imports": {"unused_thing": "_helpers"},
}

PYTEST_INI = "[pytest]\npython_files = test_*.py\npython_classes = Test*\n" \
             "python_functions = test_*\n"
PYLINTRC = "[FORMAT]\nmax-line-length=99\n"


def plan_dict() -> dict:
    """A fresh, mutable copy of the sample plan."""
    return copy.deepcopy(PLAN)


def build(root: Path, source: str = SAMPLE) -> Path:
    """Lay the sample repository out under ``root`` with ``source`` as the module, ``root`` a
    git repository (made one through :mod:`tools.ci.scratch` when it is not already), because
    the tool finds a module's importers among the files git does not ignore."""
    if not (root / ".git").exists():
        scratch.run(root, "init", "--quiet", "--initial-branch=dev")
    for path, text in {
        "pytest.ini": PYTEST_INI,
        ".pylintrc": PYLINTRC,
        "tests/__init__.py": "",
        "tests/shared.py": SHARED,
        "tests/test_area/__init__.py": "",
        "tests/test_other.py": IMPORTER,
        SOURCE: source,
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return root


def layout_of(root: Path, plan: dict | None = None) -> Layout:
    """The sample's layout under ``plan`` (the sample plan when None)."""
    rules = collect_rules(root)
    return resolve(read_module(root / SOURCE, rules), parse_plan(plan or plan_dict()), rules)


def tree(root: Path) -> dict[str, bytes]:
    """Every file of the working tree but git's own, by path: what a refusal, or a write
    that undid itself, leaves as it was."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def write_plan(root: Path, plan: dict | None = None) -> Path:
    """Write ``plan`` (the sample plan when None) to a file and return its path."""
    path = root / "sample.plan.json"
    path.write_text(json.dumps(plan or plan_dict()), encoding="utf-8")
    return path


@pytest.fixture(name="repo")
def _repo(tmp_path: Path) -> Path:
    """A fresh sample repository."""
    return build(tmp_path)
