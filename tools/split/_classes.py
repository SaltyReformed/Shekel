"""A test class too large for any one part, split across several (ruling Fork 2, beta).

The class keeps its NAME in every part, so its tests' node ids change only by file.  Its
docstring (verbatim) and every non-test member move ONCE onto a mixin, ``_<Class>Members``,
in the package's ``_helpers.py``; each part declares ``class <Class>(_<Class>Members):`` with
a one-line docstring naming its slice, then its contiguous run of the class's tests.  One
home per member, and identity for a split class is proved per MEMBER.

**What this module refuses**, because inheritance or a per-part class could change it: a
decorator, base or keyword on the class (a mark would have to be repeated, a base re-ordered
in the MRO); a class without a docstring; a non-blank line between the ``class`` line and the
docstring; a fixture member of any scope but ``function`` (each part would run it again);
``setup_class`` / ``teardown_class`` and their unittest spellings; ``super``, ``__class__``,
``self.__class__`` or ``type(self)`` (each now names a different class); a member that names
its own class; an assignment to a class attribute, or to any attribute of ``cls`` or of the
class by name (each part would hold its own); ``globals()``; and a test that reaches a test
placed in another slice through ``self``.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from tools.split._model import ClassSlice
from tools.split._source import (
    Block,
    CollectRules,
    Refusal,
    Unit,
    blocks,
    fixture_decorator,
    fixture_scope,
    is_docstring,
    member_free_names,
)

#: Class-level setup that runs once per CLASS, so once per part after a split.
_ONCE_PER_CLASS = frozenset({"setup_class", "teardown_class", "setUpClass", "tearDownClass"})

#: Names that resolve to the class a method was defined in or called on.
_CLASS_REACHING = frozenset({"super", "__class__"})


def mixin_name(class_name: str) -> str:
    """The mixin a split class's members move onto."""
    return f"_{class_name}Members"


@dataclass(frozen=True)
class ClassShape:
    """A class read for splitting: its docstring's lines, and its body as member blocks,
    each marked a test (pytest collects it) or not."""

    unit: Unit
    docstring: tuple[int, int]
    members: tuple[Block, ...]
    tests: frozenset[str]

    @property
    def name(self) -> str:
        """The class's name."""
        return self.unit.name

    def non_tests(self) -> tuple[Block, ...]:
        """The members that move onto the mixin, in source order."""
        return tuple(m for m in self.members if m.name not in self.tests)

    def test_order(self) -> tuple[str, ...]:
        """The class's test methods, in source order."""
        return tuple(m.name for m in self.members if m.name in self.tests)


def class_shape(unit: Unit, lines: tuple[str, ...], rules: CollectRules) -> ClassShape:
    """Read ``unit`` (a test class) as a :class:`ClassShape`, refusing a class whose header
    would have to change beyond the mixin base."""
    node = unit.block.node
    where = f"class {unit.name}"
    if not isinstance(node, ast.ClassDef):
        raise Refusal(f"{where}: only a class can be split")
    if node.decorator_list or node.bases or node.keywords:
        raise Refusal(f"{where}: a decorated class, or one with bases or keywords, is not "
                      "split (its marks or MRO would change)")
    if not node.body or not is_docstring(node.body[0]):
        raise Refusal(f"{where}: a split class needs a docstring to move onto its mixin")
    doc = node.body[0]
    if any(lines[n - 1].strip() for n in range(node.lineno + 1, doc.lineno)):
        raise Refusal(f"{where}: lines between the class line and its docstring")
    members = blocks(node.body[1:], doc.end_lineno, lines, where)
    tests = frozenset(m.name for m in members
                      if isinstance(m.node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and rules.is_test_function(m.name))
    return ClassShape(unit=unit, docstring=(doc.lineno, doc.end_lineno),
                      members=tuple(members), tests=tests)


def check_slices(shape: ClassShape, slices: list[ClassSlice]) -> None:
    """Refuse slices that do not partition the class's tests into contiguous runs, in the
    order the plan lists them, at least two of them."""
    where = f"class {shape.name}"
    if len(slices) < 2:
        raise Refusal(f"{where}: a split needs at least two slices; place it whole")
    order = shape.test_order()
    listed = [t for s in slices for t in s.tests]
    if listed != list(order):
        missing = sorted(set(order) - set(listed))
        extra = sorted(set(listed) - set(order))
        raise Refusal(f"{where}: the slices must list every test exactly once in source "
                      f"order; missing {missing}, unknown {extra}"
                      + ("" if missing or extra else ", or out of order"))
    if any(not s.docstring.strip() for s in slices):
        raise Refusal(f"{where}: every slice needs its one-line docstring")


#: The names a method reaches its own instance or class through.
_SELF_NAMES = frozenset({"self", "cls"})


def _is_self(node: ast.AST) -> bool:
    """Whether ``node`` is the bare name ``self`` or ``cls``."""
    return isinstance(node, ast.Name) and node.id in _SELF_NAMES


def _reaches_own_class(node: ast.AST) -> bool:
    """Whether ``node`` holds ``self.__class__`` / ``cls.__class__`` or ``type(self)``."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "__class__" and _is_self(sub.value):
            return True
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id == "type" and len(sub.args) == 1 and _is_self(sub.args[0])):
            return True
    return False


def _assigned_attributes(node: ast.AST) -> set[tuple[str, str]]:
    """Each ``<name>.<attr>`` an assignment in ``node`` writes, as ``(name, attr)``."""
    found = set()
    for sub in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
            targets = [sub.target]
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name):
                    found.add((part.value.id, part.attr))
    return found


def _self_reads(node: ast.AST) -> set[str]:
    """Each attribute read through ``self`` in ``node``."""
    return {sub.attr for sub in ast.walk(node)
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"}


def _check_member(shape: ClassShape, member: Block, lines: tuple[str, ...],
                  class_attributes: set[str]) -> None:
    """Refuse one member whose meaning could change on a mixin or a per-part class."""
    where = f"class {shape.name}.{member.name}"
    if member.name in _ONCE_PER_CLASS:
        raise Refusal(f"{where}: runs once per class, so once per part after a split")
    decorator = fixture_decorator(member.node)
    if decorator is not None and fixture_scope(decorator) != "function":
        raise Refusal(f"{where}: a fixture scoped beyond one test would run once per part")
    free = member_free_names(member.text(lines))
    reaching = (free & _CLASS_REACHING) | ({shape.name} & free)
    if reaching or _reaches_own_class(member.node):
        raise Refusal(f"{where}: reaches its own class ({sorted(reaching) or 'by type'}), "
                      "which is a different class in each part")
    if "globals" in free:
        raise Refusal(f"{where}: globals() inside a split class")
    for name, attribute in _assigned_attributes(member.node):
        if name in {"cls", shape.name} or (name == "self" and attribute in class_attributes):
            raise Refusal(f"{where}: assigns {name}.{attribute}, a class attribute each "
                          "part would hold its own copy of")


def check_split(shape: ClassShape, slices: list[ClassSlice], lines: tuple[str, ...]) -> None:
    """Refuse a split of ``shape`` into ``slices`` that the proof could not show is pure."""
    check_slices(shape, slices)
    class_attributes = {m.name for m in shape.members
                        if isinstance(m.node, (ast.Assign, ast.AnnAssign))}
    for member in shape.members:
        _check_member(shape, member, lines, class_attributes)
    slice_of = {t: i for i, s in enumerate(slices) for t in s.tests}
    for member in shape.members:
        if member.name not in shape.tests:
            continue
        crossing = sorted(r for r in _self_reads(member.node)
                          if r in slice_of and slice_of[r] != slice_of[member.name])
        if crossing:
            raise Refusal(f"class {shape.name}.{member.name}: reaches {crossing} through "
                          "self, placed in another slice")
