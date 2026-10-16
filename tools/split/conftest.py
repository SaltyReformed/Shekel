"""The fixtures and data the split tool's tests share, in ONE home every test module imports:
a throwaway repository holding one sample test module that exercises every shape the tool
moves (an autouse fixture, banner comments, a helper, constants, a parametrize list, whole
classes, a module-level test, and a class split across two parts whose tests pass, skip,
xfail and xpass), a module it imports from, an importer of one of its helpers, and a plan for
it; the readers of what the tool refuses or accepts for a source built from the sample; the
sample's collection and outcomes; and the ``split`` fixture, which runs the command line on
the sample with the arms that start a process stubbed."""
from __future__ import annotations

import copy
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.split import _dynamic as dynamic
from tools.split import _run as run
from tools.split._layout import Layout, resolve
from tools.split._model import Plan, parse_plan
from tools.split._observe import LONE, setups_path
from tools.split._render import Rewrite, line_limit, render_all, write
from tools.split._source import Refusal, collect_rules, read_module
from tools.split.split import Context, main

SOURCE = "tests/test_area/test_sample.py"

#: The package the sample's split writes in the source's place.
PACKAGE = "tests/test_area/test_sample"

#: The sample's collection, as pytest names it.
BEFORE_IDS = [
    f"{SOURCE}::TestFirst::test_one",
    f"{SOURCE}::TestFirst::test_cases[a-1]",
    f"{SOURCE}::TestFirst::test_cases[b-2]",
    f"{SOURCE}::test_top_level",
    f"{SOURCE}::TestBig::test_a",
    f"{SOURCE}::TestBig::test_b",
    f"{SOURCE}::TestBig::test_c",
    f"{SOURCE}::TestBig::test_d",
    f"{SOURCE}::TestBig::test_e",
]

#: The importer the sample's split re-points, and the line it rewrites.
IMPORTER, REPOINTED = "tests/test_other.py", 2

#: How each of the sample's tests ends.
OUTCOMES = {**dict.fromkeys(BEFORE_IDS, "passed"), f"{SOURCE}::TestBig::test_c": "skipped",
            f"{SOURCE}::TestBig::test_d": "xfailed", f"{SOURCE}::TestBig::test_e": "xpassed"}

#: Each step of ``record`` a stub can stop, as a process that did not observe would.
RECORD_STEPS = ("the source's pylint", "the importers' pylint", "the collect", "the run")

#: The shape every run had in the delta review's M-A: nothing collected, nothing run, and
#: pytest interrupted.
EMPTY = {"collected": [], "outcomes": {}, "exitstatus": 2}


def cut_fixture(root: Path) -> dict:
    """A parent conftest's module-scoped fixture set up for the source's tests: its one
    instance per module the split would cut into one per part (fork F)."""
    return {"argname": "area", "scope": "module", "baseid": "tests",
            "file": str(root / "tests/conftest.py"), "by": SOURCE}

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

IMPORTER_TEXT = textwrap.dedent('''\
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


def plan_bytes(plan: dict | None = None) -> bytes:
    """The bytes of a plan file holding ``plan`` (the sample plan when None), as
    :func:`write_plan` and the ``split`` fixture write one."""
    return json.dumps(plan or plan_dict()).encode("utf-8")


def plan_of(plan: dict | None = None) -> Plan:
    """``plan`` (the sample plan when None) as the tool reads a file holding it
    (:func:`plan_bytes`), its sha256 that file's."""
    return parse_plan(plan_bytes(plan), "the sample plan")


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
        IMPORTER: IMPORTER_TEXT,
        SOURCE: source,
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return root


def layout_of(root: Path, plan: dict | None = None) -> Layout:
    """The sample's layout under ``plan`` (the sample plan when None)."""
    rules = collect_rules(root)
    return resolve(read_module(root / SOURCE, rules), plan_of(plan), rules)


def refusal(tmp_path: Path, source: str = SAMPLE, plan: dict | None = None) -> str:
    """The refusal message for ``source`` under ``plan``; fails if the tool accepts it."""
    root = build(tmp_path, source)
    with pytest.raises(Refusal) as caught:
        render_all(layout_of(root, plan), line_limit(root))
    return str(caught.value)


def accepted(tmp_path: Path, source: str = SAMPLE, plan: dict | None = None) -> None:
    """Fail unless the tool lays ``source`` out under ``plan`` with no refusal."""
    root = build(tmp_path, source)
    assert render_all(layout_of(root, plan), line_limit(root))


def sample_with(old: str, new: str) -> str:
    """The sample with ``old`` (which must occur once) replaced by ``new``."""
    assert SAMPLE.count(old) == 1, old
    return SAMPLE.replace(old, new)


def written(layout: Layout, root: Path, texts: dict[str, str], rewrites: list[Rewrite],
            beside: dict[Path, str]) -> None:
    """The apply's writes (:func:`tools.split._render.write`) with nothing run inside their
    unit: the tree as the writes alone leave it."""
    with write(layout, root, texts, rewrites, beside):
        pass


def tree(root: Path) -> dict[str, bytes]:
    """Every file of the working tree but git's own, by path: what a refusal, or a write
    that undid itself, leaves as it was."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def write_plan(root: Path, plan: dict | None = None) -> Path:
    """Write ``plan`` (the sample plan when None) to a file and return its path."""
    path = root / "sample.plan.json"
    path.write_bytes(plan_bytes(plan))
    return path


@pytest.fixture(name="repo")
def _repo(tmp_path: Path) -> Path:
    """A fresh sample repository."""
    return build(tmp_path)


class Stubs:
    """The process arms, answering as a faithful run of the sample would, and what a case
    changes: the importer's pylint messages after the split, the plan gate's verdict, the
    fixtures the record run set up, the step of a record that stops (``stop_at``, one of
    :data:`RECORD_STEPS`), and what each kind of run observed instead (``changed``, keyed
    ``"<source|split|alone> <collect|run>"``)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        """Answer under ``mapping``, the sample's old -> new id map."""
        self.mapping = mapping
        self.importer_after: list[dict] = []
        self.gate = (0, "3 passed")
        self.setups: list[dict] = []
        self.recorded = False
        self.stop_at: str | None = None
        self.changed: dict[str, dict] = {}

    def _stop(self, step: str) -> None:
        """Stop the run at ``step`` when the case says so."""
        if self.stop_at == step:
            raise run.RunError(f"stopped at {step}")

    def observe(self, _root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
        """The run of ``targets``: the source before, the package or one part after."""
        target = targets[0]
        if target == SOURCE:
            self._stop("the collect" if collect_only else "the run")
            outcomes, kind = OUTCOMES, "source"
        else:
            outcomes = {new: OUTCOMES[old] for old, new in self.mapping.items()
                        if new.startswith(target if target.endswith(".py") else target + "/")}
            kind = "alone" if target.endswith(".py") else "split"
        observed = {"collected": sorted(outcomes) if collect_only else [], "deselected": [],
                    "outcomes": {} if collect_only else outcomes, "exitstatus": 0,
                    **self.changed.get(f"{kind} {'collect' if collect_only else 'run'}", {})}
        out.write_text(json.dumps(observed), encoding="utf-8")
        setups_path(out, LONE).write_text(json.dumps({
            "setups": self.setups if target == SOURCE else [],
            "ran": sorted(observed["outcomes"])}), encoding="utf-8")
        return observed

    def pylint(self, _root: Path, paths: list[str]) -> list[dict]:
        """No message anywhere, but the importer's after the split."""
        if paths == [IMPORTER]:
            self._stop("the importers' pylint")
            after, self.recorded = (self.importer_after if self.recorded else []), True
            return after
        if paths == [SOURCE]:
            self._stop("the source's pylint")
        return []


@pytest.fixture(name="split")
def _split(tmp_path, monkeypatch):
    """The sample committed to a throwaway repository, the process arms stubbed; a function
    running one command through ``main``, returning its exit status."""
    root = scratch.repository(tmp_path)
    build(root)
    committed(root, "the sample")
    stubs = Stubs(dynamic.id_map(layout_of(root), BEFORE_IDS))
    monkeypatch.setattr(run, "observe", stubs.observe)
    monkeypatch.setattr(run, "pylint_messages", stubs.pylint)
    monkeypatch.setattr(run, "plan_gate", lambda _root: stubs.gate)
    plan = tmp_path / "test_sample.plan.json"
    plan.write_bytes(plan_bytes())

    def command(name: str) -> int:
        args = [name, str(plan), "--into", str(command.into)]
        if name != "record":
            args += ["--idmap", str(command.idmap)]
        return main(args, root=root)
    command.stubs, command.root, command.plan = stubs, root, plan
    command.into, command.idmap = tmp_path / "record", tmp_path / "test_sample.idmap.json"
    return command


def committed(root: Path, message: str) -> None:
    """Commit every change in ``root``'s working tree, so a case's edit is ``HEAD``'s and the
    tree is clean, as ``record`` and ``apply`` require (REVISED (B))."""
    scratch.run(root, "add", "-A")
    scratch.run(root, "commit", "--quiet", "-m", message)


def applied(split) -> None:
    """Record and apply the sample's split, each proved."""
    assert split("record") == 0
    assert split("apply") == 0


def held(split, name: str) -> Path:
    """Where the complete record in ``split.into`` holds the file ``name``, as the tool finds
    it (:meth:`tools.split.split.Context.held`): in the directory of the run its token
    names."""
    return Context(plan=plan_of(), root=split.root, into=split.into).bound().held(name)


def only_on_path(tmp_path: Path, monkeypatch, *programs: str) -> None:
    """Make ``PATH`` reach ``programs`` alone, each linked from where it is now."""
    reached = tmp_path / "bin"
    reached.mkdir()
    for program in programs:
        (reached / program).symlink_to(shutil.which(program))
    monkeypatch.setenv("PATH", str(reached))


def everything(split) -> tuple:
    """Every file an apply may touch: the tree, the record, and the ``--idmap`` file (None
    when there is none)."""
    return (tree(split.root), tree(split.into),
            split.idmap.read_bytes() if split.idmap.exists() else None)
