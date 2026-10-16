"""A split PLAN as the tool reads it: one source test module, the parts it becomes, and where
each import nothing uses is placed.

The plan names only what a person decides.  Everything the source already says is derived,
so it has one home: the package directory is the source's path without ``.py``; every
top-level fixture goes to the package's ``conftest.py``; every helper no part names goes to
its ``_helpers.py``; a split class's non-test members go to the mixin there.  A plan that
names an unknown key, or a node twice, is refused rather than read generously.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tools.split._source import load_json, text_from

#: The package modules the tool writes itself, which no part may be named.
HELPERS = "_helpers"
CONFTEST = "conftest"
INIT = "__init__"
RESERVED = frozenset({HELPERS, CONFTEST, INIT})


class PlanError(ValueError):
    """The plan file is malformed: a missing or unknown key, or a value of the wrong type."""


@dataclass(frozen=True)
class ClassSlice:
    """A contiguous run of one class's tests, placed in one part: the class is too large for
    any single part, so its tests are partitioned in source order across several."""

    class_name: str
    tests: tuple[str, ...]
    docstring: str


@dataclass(frozen=True)
class Part:
    """One ``test_<subject>.py`` the source becomes: its module name, its docstring, and the
    top-level nodes (by name) or class slices it holds."""

    module: str
    docstring: str
    nodes: tuple[str | ClassSlice, ...]

    def node_names(self) -> tuple[str, ...]:
        """The top-level names this part holds, a sliced class by its class name."""
        return tuple(n.class_name if isinstance(n, ClassSlice) else n for n in self.nodes)


@dataclass(frozen=True)
class Plan:
    """The whole plan for one source module, and the sha256 of the bytes it was parsed from,
    which a record keeps: a record rests on its plan as on ``HEAD`` and the environment, so
    it is judged only under the plan it was taken under (:func:`tools.split.split._held_to`;
    the coordinator's ruling on the lane's report at 3C-2, the fourteenth unit correction,
    2026-10-16)."""

    source: PurePosixPath
    parts: tuple[Part, ...]
    unused_imports: dict[str, str]
    sha256: str

    @property
    def package(self) -> PurePosixPath:
        """The package directory the source becomes: its own path without ``.py``."""
        return self.source.with_suffix("")

    @property
    def dotted(self) -> str:
        """The source module's dotted import path (``tests.test_area.test_sample``)."""
        return ".".join(self.package.parts)

    @property
    def stem(self) -> str:
        """The source module's own name, which the plan and id map files are named after."""
        return self.source.stem


def _keys(where: str, raw: dict, required: set[str], optional: frozenset[str] = frozenset()
          ) -> None:
    """Refuse ``raw`` unless it holds every required key and nothing else."""
    if not isinstance(raw, dict):
        raise PlanError(f"{where}: expected an object, got {type(raw).__name__}")
    missing = required - raw.keys()
    unknown = raw.keys() - required - optional
    if missing or unknown:
        raise PlanError(f"{where}: missing {sorted(missing)}, unknown {sorted(unknown)}")


def _text(where: str, value: object) -> str:
    """``value`` as a non-empty string, or a refusal naming ``where``."""
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _node(where: str, raw: object) -> str | ClassSlice:
    """One entry of a part's ``nodes``: a top-level name, or a class slice object."""
    if isinstance(raw, str):
        return _text(where, raw)
    _keys(where, raw, {"class", "tests", "docstring"})
    tests = raw["tests"]
    if not isinstance(tests, list) or not tests:
        raise PlanError(f"{where}: 'tests' must be a non-empty list of names")
    docstring = _text(f"{where}.docstring", raw["docstring"])
    if "\n" in docstring:
        raise PlanError(f"{where}: a slice's class docstring is ONE line")
    return ClassSlice(
        class_name=_text(f"{where}.class", raw["class"]),
        tests=tuple(_text(f"{where}.tests", t) for t in tests),
        docstring=docstring,
    )


def _part(where: str, raw: object) -> Part:
    """One entry of the plan's ``parts``."""
    _keys(where, raw, {"module", "docstring", "nodes"})
    module = _text(f"{where}.module", raw["module"])
    if not module.isidentifier() or module in RESERVED:
        raise PlanError(f"{where}: {module!r} is not a usable module name")
    nodes = raw["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise PlanError(f"{where}: 'nodes' must be a non-empty list")
    return Part(
        module=module,
        docstring=_text(f"{where}.docstring", raw["docstring"]),
        nodes=tuple(_node(f"{where}.nodes[{i}]", n) for i, n in enumerate(nodes)),
    )


def parse_plan(data: bytes, where: str) -> Plan:
    """Build a :class:`Plan` from a plan file's bytes, read from ``where``: refused, naming
    ``where``, when they are not UTF-8 JSON (:func:`tools.split._source.text_from`,
    :func:`tools.split._source.load_json`), and refusing any shape it does not expect.  One
    bytes object is decoded, parsed and hashed, so every field of the plan and its sha256
    are of the same bytes."""
    raw = load_json(text_from(data, where), where)
    _keys("plan", raw, {"source", "parts"}, frozenset({"unused_imports"}))
    source = PurePosixPath(_text("plan.source", raw["source"]))
    if source.suffix != ".py" or source.is_absolute() or ".." in source.parts:
        raise PlanError(f"plan.source: {source} is not a repository-relative .py path")
    parts = raw["parts"]
    if not isinstance(parts, list) or len(parts) < 2:
        raise PlanError("plan.parts: a split makes at least two parts")
    unused = raw.get("unused_imports", {})
    if not isinstance(unused, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in unused.items()):
        raise PlanError("plan.unused_imports: expected an object of alias -> module name")
    built = tuple(_part(f"plan.parts[{i}]", p) for i, p in enumerate(parts))
    modules = [p.module for p in built]
    if len(set(modules)) != len(modules):
        raise PlanError(f"plan.parts: a module name is used twice in {modules}")
    return Plan(source=source, parts=built, unused_imports=dict(unused),
                sha256=hashlib.sha256(data).hexdigest())


def load_plan(path: Path) -> Plan:
    """The plan file at ``path``, read once and parsed (:func:`parse_plan`)."""
    return parse_plan(path.read_bytes(), str(path))
