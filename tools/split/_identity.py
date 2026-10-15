"""The identity screen: what code being moved may read of WHERE it runs.

A split moves code one directory deeper into a part, and may split a class across parts.
Every value naming where code runs (its module, its file, its lines, its frames, the class a
split member now sits on) then changes while the code reads the same, so a test reading one
can keep passing for a different reason.  The screen is a GRAMMAR, not a list of spellings
(the coordinator's ruling on the delta review's H-B, 2026-10-15: nine reviews each found the
next spelling of a denylist): it names the reads that may change, accepts them in ONE shape,
and refuses every other.  A refused file is rewritten in its own commit before it is split.

**The identity set** (:data:`IDENTITY`), from Python's data model: each attribute whose value
differs between the source module and a part for an object the move relocates.

* A module's own: ``__file__`` (the part's file, one directory deeper), ``__cached__`` (its
  compiled file), ``__spec__`` and ``__loader__`` (bound to the part's name and file),
  ``__package__`` (the new package), ``__path__`` (a package's search path).
* A function's or class's: ``__module__`` (the part's name), ``__globals__`` (the part's
  namespace), ``__code__`` (code whose file and lines are the part's), ``__firstlineno__``
  (a class's first line, which the move renumbers).
* ``__dict__``: a module's namespace, or a split class's, which holds one slice.
* A code object's: ``co_filename``, ``co_firstlineno``, ``co_lines``, ``co_positions``,
  ``co_linetable`` and ``co_lnotab`` (its file and lines), ``co_qualname`` (a split member's
  is the mixin's).
* A frame's: ``f_globals`` and ``f_locals`` (a module frame's namespace), ``f_code``,
  ``f_lineno``, ``f_back`` (the caller's frame, in the part).
* A traceback's: ``tb_frame``, ``tb_lineno``, ``tb_next``; and an exception's
  ``__traceback__``, whose frames run in the part.
* A generator's, coroutine's and asynchronous generator's: ``gi_frame``, ``gi_code``,
  ``cr_frame``, ``cr_code``, ``cr_origin`` (the file and line that created it), ``ag_frame``,
  ``ag_code``.

The attributes a module shares with functions and classes (``__name__``, ``__doc__``,
``__annotations__``, ``__qualname__``) differ only on a module or a split class, so they are
refused where those are reached: no moved code can hold its own module (every route the
standard library offers to it is refused below), and on a split class they are
:data:`SPLIT_CLASS`, refused off ``self``, ``cls`` and ``request``.  Read as a BARE name,
every module dunder is the module's own, and :mod:`tools.split._layout` refuses it.

**Two more attribute sets**, refused in the same shape as the identity set.
:data:`CARRIER_REACH`: an attribute that hands back an object the no-aliasing rule below
protects, reads one named only at run time, or hands back the builtins namespace.
:data:`SPLIT_CLASS`: what differs for a class split across parts (its name, qualname,
docstring, namespace, bases and order).

**The one exempt shape** (rule 3): an identity read is accepted only off a PURE attribute
chain (``name.attr.attr``, no call or subscript anywhere in it) whose root, resolved by
:mod:`symtable` in the scope that reads it, is bound there ONLY by import statements (any
assignment, parameter, loop, ``with`` or ``except`` target, walrus, comprehension variable
or ``del`` disqualifies it), when no binding import, and nothing the chain then traverses
into, is the source module or a module of the package being written.  A name ANY import of
the source or its package binds, anywhere in the source, is never exempt.  An identity name
as a STRING constant is refused anywhere (rule 2): ``getattr(x, "__module__")``,
``vars(x)["__file__"]``; so is a string naming the source or anything in it (a dotted target
such as ``monkeypatch.setattr("<source>.NAME", ...)`` is resolved by importing it), but in a
bare string statement, a docstring, which reaches nothing.

**References refused whatever is done with them** (rule 4, aliases resolved by binding):
:data:`REFUSED_REFERENCES` (the import system, frames, tracebacks, :mod:`inspect`) and
:data:`REFUSED_BUILTINS`, but the reach rule's ``<x>.setitem(globals(), ...)``
(:func:`tools.split._layout.globals_keys`); a direct call ``inspect.<fn>(...)`` spelled as a
pure chain off an import-bound :mod:`inspect` with at least one argument, every one an exempt
chain (with none, it reads the running frame); and, called directly (Josh's answer to the
census's third fork, 2026-10-15), ``importlib.reload`` of one exempt chain, and
``importlib.import_module`` / ``__import__`` of one constant absolute name that neither is nor
holds the source (each ``__import__`` ``fromlist`` entry judged too, and with none the top
package it returns).  Also refused: any chain an import binds that reaches the source or a
package holding it (after the split such an import names the package the split makes, so a
write through it would land beside the code it meant to patch); and a module
:data:`REFUSED_REFERENCES` or :data:`CARRIER_MODULES` names held as ANOTHER's attribute
(``os.sys.modules``), judged as that module, or used as a value, unless called directly (a
module cannot be called, so ``sqlalchemy.inspect(obj)`` is a function).
:data:`CARRIER_MODULES` may be named only as the root of an attribute chain, so no other name
holds the module whose members are refused.  ``getattr`` and ``hasattr`` with a name computed
at run time are refused off any object but an exempt chain, and ``operator.attrgetter`` /
``methodcaller`` unless called directly with constant names: the screen cannot know the name.
``setattr`` and ``delattr`` WRITE, and are not refused for a computed name (Josh's answer to
the first fork): a write can land somewhere the move changes only on the module itself or a
split class, and neither is within reach: the module by the rules here, a split class by
rule 5, :mod:`tools.split._classes` and :mod:`tools.split._layout` (no node outside the class
may name it).

**No aliasing of the runtime's identity carriers** (rule 5): the fixture ``request``, and
every attribute of ``request`` that is the requesting node, an ancestor, the session holding
it or the test's own object (:data:`REQUEST_CARRIERS`), appear ONLY as the root of an
attribute chain or the callee of a call: never bound to another name, passed, returned,
yielded, compared or stored.  So the rules keyed on those names are sound, because no other
name can hold those objects: :data:`REQUEST_LOCATION` (and any private attribute) read
through ``request``, and :data:`SPLIT_CLASS` read through ``request``.  ``self`` and ``cls``
are held to the same rule in a split class's members (:data:`SPLIT_CARRIERS`), where every
rule keyed on their names runs (:mod:`tools.split._classes`; a split class has no bases, so
no other class's methods receive its ``self``); there one of the class's methods may only be
called through them (``self.m()``), since a bound member's ``__self__``, ``__func__`` and
qualname name the mixin or a slice.  Outside a split class the identity set is refused off
every root, so they are free (Josh's answer to the second fork).  A ``request`` bound by an
import is not the fixture (Flask's is a request context) and is not held to these rules.

**A relative import inside moved code** is refused: a part sits one directory deeper, so its
dots would name another module (the source reader refuses one at module level already).

**Not modelled, by name:** a library outside the standard library that hands back a module,
a frame or a location record (a log record's ``pathname``: ``git grep`` over ``tests/``
found no read, 2026-10-15); the default ``repr`` of a class or of its instance, which prints
its module.  A test ASSERTING one fails the outcome arm after the split.
"""
from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass, field

from tools.split._model import Plan
from tools.split._source import Imported, SourceModule, attribute_read, imported, within

#: The identity set: attributes whose value differs between the source module and a part
#: for an object the move relocates (the module docstring gives each one's reason).
IDENTITY = frozenset({
    "__file__", "__cached__", "__spec__", "__loader__", "__package__", "__path__",
    "__module__", "__globals__", "__code__", "__firstlineno__", "__dict__",
    "co_filename", "co_firstlineno", "co_lines", "co_positions", "co_linetable", "co_lnotab",
    "co_qualname",
    "f_globals", "f_locals", "f_code", "f_lineno", "f_back",
    "tb_frame", "tb_lineno", "tb_next", "__traceback__",
    "gi_frame", "gi_code", "cr_frame", "cr_code", "cr_origin", "ag_frame", "ag_code",
})

#: Attributes that hand back an object the no-aliasing rule protects (a bound method's
#: ``__self__``; a closure's cells, one of which may hold ``self``), that read an attribute
#: named only at run time (``__getattribute__``, ``__getattr__``), or that hand back the
#: builtins namespace (a function's ``__builtins__``), where ``__import__``, ``eval`` and
#: ``globals`` are reached by a subscript.
CARRIER_REACH = frozenset({"__self__", "__closure__", "cell_contents", "__getattribute__",
                           "__getattr__", "__builtins__"})

#: Every attribute read refused but in the exempt shape, and refused as a string anywhere.
_REFUSED_READS = IDENTITY | CARRIER_REACH

#: What differs for a class split across parts: its name and qualname (a member on the
#: mixin is ``_<Class>Members``'s), its docstring (each slice states its own), its namespace
#: (one slice's members), its bases and method order (the mixin is one), its first line and
#: the attributes its methods assign.
SPLIT_CLASS = frozenset({"__name__", "__qualname__", "__doc__", "__dict__", "__bases__",
                         "__base__", "__mro__", "mro", "__firstlineno__",
                         "__static_attributes__"})

#: Dotted names whose every reference is refused, each with everything inside it: the
#: import system (a module by name, or run by name), the collector's walks over every live
#: object (the module among them), frames and tracebacks (where code runs), and
#: :mod:`inspect` (which reads both), with the call shapes the module docstring names.
REFUSED_REFERENCES = frozenset({
    "sys.modules", "sys._getframe", "sys._getframemodulename", "sys._current_frames",
    "sys.exc_info", "sys.exception", "sys.last_traceback", "sys.last_exc", "sys.last_value",
    "importlib", "runpy.run_module", "gc.get_objects", "gc.get_referrers", "gc.get_referents",
    "traceback", "inspect",
    "builtins.__import__", "builtins.eval", "builtins.exec", "builtins.compile",
    "builtins.globals", "builtins.locals", "builtins.vars",
})

#: Builtins whose every reference is refused: each imports a module by name, runs code
#: compiled where the caller says, or reads the namespace of the module it runs in.
REFUSED_BUILTINS = frozenset({"__import__", "eval", "exec", "compile", "globals", "locals",
                              "vars"})

#: Modules holding a refused member, so no other name may hold them.
CARRIER_MODULES = frozenset({"sys", "builtins", "gc", "runpy"})

#: The modules :data:`REFUSED_REFERENCES` and :data:`CARRIER_MODULES` name, which another
#: module may hold as an attribute (``os.sys``): such an attribute is judged as the module it
#: may be.
_MODULE_NAMED = frozenset(r.split(".")[0] for r in REFUSED_REFERENCES) | CARRIER_MODULES

#: Import-system calls the screen can resolve, accepted when called directly as the module
#: docstring says (Josh's answer to the census's third fork, 2026-10-15).
_IMPORT_CALLS = frozenset({"importlib.import_module", "importlib.reload",
                           "builtins.__import__"})

#: Builtins that READ an attribute named by their second argument.  ``setattr`` and
#: ``delattr`` write, and a write lands somewhere the move changes only on the module itself
#: or a split class, neither within reach (the module docstring; Josh's answer to the
#: census's first fork).
_BY_NAME = frozenset({"getattr", "hasattr"})

#: Callables that read an attribute named by their arguments off an object supplied only
#: when the callable they return is called, so no exempt chain can be judged.
_NAME_GETTERS = frozenset({"operator.attrgetter", "operator.methodcaller"})

#: The runtime's identity carriers held to the no-aliasing rule only in a split class's
#: members, where every rule keyed on their names runs (Josh's answer to the census's second
#: fork, 2026-10-15); the fixture ``request`` is held to it everywhere.
SPLIT_CARRIERS = frozenset({"self", "cls"})

#: Attributes of a fixture's ``request``, or of its node, that ARE the requesting node, an
#: ancestor, the session holding every node, or the requesting test's own object.
REQUEST_CARRIERS = frozenset({"node", "parent", "session", "cls", "function", "obj",
                              "instance", "module"})

#: Attributes of a fixture's ``request``, or of its node, that name or lead to the requesting
#: module, its file, or the node's place in the tree (``keywords`` holds every parent's name,
#: the module's file among them; ``ihook`` is the hooks of the conftests above its file).
REQUEST_LOCATION = frozenset({"module", "fspath", "path", "nodeid", "location", "reportinfo",
                              "parent", "getparent", "listchain", "iter_parents", "keywords",
                              "ihook"})


@dataclass
class _Scope:
    """One scope of the source as Python resolves names in it: its symbol table (None when
    it cannot be matched unambiguously, which makes every name read in it unresolved), the
    scope enclosing it, whether it is a class body, and the import statements directly in
    it."""

    table: symtable.SymbolTable | None
    parent: _Scope | None
    is_class: bool = False
    imports: list[ast.Import | ast.ImportFrom] = field(default_factory=list)


@dataclass(frozen=True)
class _Seen:
    """Where one node of the moved code sits: its scope, and the names the comprehensions
    around it bind (Python 3.12+ inlines a list, set or dict comprehension into the
    enclosing table, which then reports its variable as whatever else binds that name)."""

    scope: _Scope
    shadow: frozenset[str]


class _Tables:
    """The source's symbol tables, each function and class body found by its name and
    line."""

    def __init__(self, source: SourceModule) -> None:
        """Read the whole source's symbol table once."""
        self.module = symtable.symtable("\n".join(source.lines) + "\n", str(source.path),
                                        "exec")
        self.by_key: dict[tuple[str, int], list[symtable.SymbolTable]] = {}
        pending = [self.module]
        while pending:
            table = pending.pop()
            self.by_key.setdefault((table.get_name(), table.get_lineno()), []).append(table)
            pending.extend(table.get_children())

    def find(self, name: str, line: int, kind: str) -> symtable.SymbolTable | None:
        """The one table of ``kind`` (``function`` or ``class``) named ``name`` that starts on
        ``line``, or None when there is none or more than one."""
        found = [t for t in self.by_key.get((name, line), [])
                 if str(t.get_type()).lower().endswith(kind)]
        return found[0] if len(found) == 1 else None


def _comprehension_targets(node: ast.AST) -> frozenset[str]:
    """Every name a comprehension's generators bind."""
    return frozenset(sub.id for gen in node.generators for sub in ast.walk(gen.target)
                     if isinstance(sub, ast.Name))


class _Walk:
    """Every node of the code being moved, each with the scope Python evaluates it in: a
    function's decorators, defaults and annotations, a class's bases, and a comprehension's
    first iterable in the enclosing scope; a body in its own.  ``members`` names the methods
    of the split class the nodes are members of, None when they are not a split class's."""

    def __init__(self, tables: _Tables, members: frozenset[str] | None) -> None:
        """Walk against ``tables``."""
        self.tables = tables
        self.members = members
        self.seen: dict[int, _Seen] = {}
        self.parents: dict[int, ast.AST] = {}
        self.nodes: list[ast.AST] = []

    def visit(self, node: ast.AST, seen: _Seen, parent: ast.AST | None = None) -> None:
        """Record ``node`` where it is evaluated, then everything under it."""
        self.seen[id(node)] = seen
        if parent is not None:
            self.parents[id(node)] = parent
        self.nodes.append(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seen.scope.imports.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            self._function(node, seen)
        elif isinstance(node, ast.ClassDef):
            self._class(node, seen)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            self._comprehension(node, seen)
        else:
            for child in ast.iter_child_nodes(node):
                self.visit(child, seen, node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
                  seen: _Seen) -> None:
        """A function's decorators, defaults and annotations where it is defined; its body in
        its own scope."""
        name = "lambda" if isinstance(node, ast.Lambda) else node.name
        inner = _Seen(_Scope(self.tables.find(name, node.lineno, "function"), seen.scope),
                      frozenset())
        body = [node.body] if isinstance(node, ast.Lambda) else node.body
        for child in ast.iter_child_nodes(node):
            self.visit(child, inner if child in body else seen, node)

    def _class(self, node: ast.ClassDef, seen: _Seen) -> None:
        """A class's decorators, bases and keywords where it is defined; its body in its own
        scope."""
        inner = _Seen(_Scope(self.tables.find(node.name, node.lineno, "class"), seen.scope,
                             is_class=True), frozenset())
        for child in ast.iter_child_nodes(node):
            self.visit(child, inner if child in node.body else seen, node)

    def _comprehension(self, node: ast.AST, seen: _Seen) -> None:
        """A comprehension's first iterable where it is written; the rest with its variables
        shadowing every other binding of their names."""
        first = node.generators[0].iter
        scope = seen.scope
        if isinstance(node, ast.GeneratorExp):
            scope = _Scope(self.tables.find("genexpr", node.lineno, "function"), seen.scope)
        inner = _Seen(scope, seen.shadow | _comprehension_targets(node))
        self.visit(first, seen, node.generators[0])
        for child in ast.iter_child_nodes(node):
            if child is not first:
                self._visit_skipping(child, first, inner, node)

    def _visit_skipping(self, node: ast.AST, skip: ast.AST, seen: _Seen,
                        parent: ast.AST) -> None:
        """Visit ``node`` but not ``skip`` under it (a comprehension's first iterable, which
        its first generator holds)."""
        if isinstance(node, ast.comprehension) and node.iter is skip:
            self.seen[id(node)] = seen
            self.parents[id(node)] = parent
            self.nodes.append(node)
            for child in ast.iter_child_nodes(node):
                if child is not skip:
                    self.visit(child, seen, node)
            return
        self.visit(node, seen, parent)


@dataclass(frozen=True)
class _Binding:
    """How a name read in one scope is bound: ``kind`` is ``import`` (bound there only by
    import statements, which ``imports`` lists), ``builtin``, ``other``, or ``unknown`` (no
    table to read)."""

    kind: str
    imports: tuple = ()


class Screen:
    """The identity screen over one source module, for one plan (the source's dotted name
    and package)."""

    def __init__(self, source: SourceModule, plan: Plan) -> None:
        """Read the source's symbol tables and every import of it, once."""
        self.source = source
        self.dotted = plan.dotted
        self.tables = _Tables(source)
        self.module_scope = _Scope(self.tables.module, None)
        self.module_scope.imports = [stmt.node for stmt in source.imports]
        self.unit_names = {unit.name for unit in source.units}
        self.statements = [stmt.node for stmt in source.imports] + [
            sub for unit in source.units for sub in ast.walk(unit.block.node)
            if isinstance(sub, ast.Import) or (isinstance(sub, ast.ImportFrom) and not sub.level)]
        self.never_foreign = frozenset(
            found.bound for node in self.statements
            for found in (imported(node, alias, self.package) for alias in node.names)
            if found.names(self.dotted))

    @property
    def package(self) -> str:
        """The source's own package (its ``__package__``), against which a relative import
        in it is resolved."""
        return self.dotted.rpartition(".")[0]

    def refusals(self, nodes: tuple[ast.AST, ...], class_name: str | None, split: bool
                 ) -> list[str]:
        """Every reason moving ``nodes`` could change what they read of where they run, each
        with its line.

        ``class_name`` is the split class the nodes are members of (None for top-level
        units), whose scope their decorators are evaluated in and whose methods a bound
        member reaches; ``split`` says the plan splits a class, so what :data:`SPLIT_CLASS`
        names may read a slice through ``request``."""
        scope = self.module_scope
        members = None
        if class_name is not None:
            node = self.source.unit(class_name).block.node
            scope = _Scope(self.tables.find(class_name, node.lineno, "class"),
                           self.module_scope, is_class=True)
            members = _methods(node)
        walk = _Walk(self.tables, members)
        for node in nodes:
            walk.visit(node, _Seen(scope, frozenset()))
        reach = _reach_patches(walk.nodes)
        return [f"line {getattr(node, 'lineno', '?')}: {why}" for node, why in (
            (node, self._check(walk, node, reach, split)) for node in walk.nodes) if why]

    def _check(self, walk: _Walk, node: ast.AST, reach: set[int], split: bool) -> str | None:
        """Why one node could change what it reads once moved, or None."""
        if isinstance(node, ast.ImportFrom) and node.level:
            return ("a relative import inside moved code: a part sits one directory deeper, "
                    "so its dots would name another module")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._check_string(walk, node)
        if isinstance(node, ast.Name):
            return self._check_name(walk, node, reach)
        if isinstance(node, ast.Call):
            why = self._check_by_name(walk, node)
            if why:
                return why
        if isinstance(node, ast.Attribute):
            why = (self._check_request_carrier(walk, node)
                   or self._check_bound_member(walk, node) or self._check_reexport(walk, node))
            if why:
                return why
        read = attribute_read(node)
        return None if read is None else self._check_read(walk, read, split)

    def _check_string(self, walk: _Walk, node: ast.Constant) -> str | None:
        """Rule 2, and a string naming the module being split: a dotted target
        (``monkeypatch.setattr("<source>.NAME", ...)``) is resolved by importing it, and after
        the split it names the package.  A bare string statement, a docstring, reaches
        nothing."""
        if node.value in _REFUSED_READS:
            return f"the string {node.value!r} names an attribute the screen refuses"
        if within(node.value, self.dotted) and not isinstance(walk.parents.get(id(node)),
                                                              ast.Expr):
            return (f"the string {_spelled(node)} names the module being split, which the "
                    "split makes a package")
        return None

    def _check_read(self, walk: _Walk, read: tuple[ast.expr, str], split: bool) -> str | None:
        """Why reading ``read[1]`` off ``read[0]`` could change once moved, or None."""
        target, attribute = read
        if attribute in _REFUSED_READS and not self._exempt(walk, target):
            return (f"{_spelled(target)}.{attribute} may name or reach where the code runs, "
                    "and it is not read off an import of another module")
        if _root_name(target) != "request" or self._import_bound(walk, _root(target)):
            return None
        if attribute in REQUEST_LOCATION or (attribute.startswith("_")
                                             and not attribute.startswith("__")):
            return (f"request...{attribute} names the requesting module, its file or its "
                    "place in the tree")
        if split and attribute in SPLIT_CLASS:
            return (f"request...{attribute} may read a split class, which each part holds a "
                    "slice of")
        return None

    def _check_name(self, walk: _Walk, node: ast.Name, reach: set[int]) -> str | None:
        """Rules 4 and 5 for one name: a refused reference, an alias of a carrier module, or
        an identity carrier used as anything but a chain's root or a callee."""
        if self._is_carrier(walk, node):
            if not (_as_root(walk, node) and isinstance(node.ctx, ast.Load)):
                return (f"{node.id} is used as a value (bound, passed, returned, compared or "
                        "stored), so another name could hold what the rules keyed on it read")
            return None
        if not isinstance(node.ctx, ast.Load):
            return None
        binding = self._resolved(walk, node)
        if binding.kind == "builtin":
            return self._check_builtin(walk, node, reach)
        if binding.kind != "import":
            return None
        return self._check_reference(walk, node, binding)

    def _check_builtin(self, walk: _Walk, node: ast.Name, reach: set[int]) -> str | None:
        """Rule 4 for a builtin: a refused one but in the shapes the module docstring names,
        and a by-name reader used as anything but a callee."""
        if (node.id in REFUSED_BUILTINS and id(node) not in reach
                and not self._import_call(walk, node, f"builtins.{node.id}")):
            return f"{node.id} reads, imports or compiles by a name or a namespace"
        if node.id in _BY_NAME and not _called(walk, node):
            return f"{node.id} is used as a value, so its calls cannot be read"
        return None

    def _is_carrier(self, walk: _Walk, node: ast.Name) -> bool:
        """Whether ``node`` names a runtime identity carrier held to rule 5 where it is read:
        the fixture ``request`` (not a ``request`` an import binds, which is Flask's), or
        :data:`SPLIT_CARRIERS` in a split class's members."""
        if node.id == "request":
            return not self._import_bound(walk, node)
        return node.id in SPLIT_CARRIERS and walk.members is not None

    def _check_reference(self, walk: _Walk, node: ast.Name, binding: _Binding) -> str | None:
        """Rule 4 for an import-bound name: what the attribute chain it roots refers to under
        each import binding it."""
        chain = _chain(walk, node)
        for found in binding.imports:
            why = self._reference(walk, chain, found)
            if why:
                return why
        return None

    def _reference(self, walk: _Walk, chain: list[ast.AST], found: Imported) -> str | None:
        """Why ``chain``, its root bound by the import ``found``, reaches where code runs:
        the module being split or a package holding it (an import of it names the package
        the split makes), a carrier module used as a value, an attribute read by a name
        computed at run time, the import system, frames or :mod:`inspect` (but the calls the
        module docstring names), or such a module re-exported by another."""
        spelled = _spelled(chain[-1])
        if found.refers in CARRIER_MODULES and len(chain) == 1:
            return (f"{spelled} (the module {found.refers}) is used as a value, so another "
                    "name could reach its refused members")
        full = ".".join([found.refers, *(step.attr for step in chain[1:])])
        if within(full, self.dotted) or within(self.dotted, full):
            return (f"{spelled} reaches the module being split, or a package holding it, "
                    "through an import of it, which names the package the split makes")
        if full in _NAME_GETTERS and not self._constant_getter(walk, chain[-1], full):
            return (f"{spelled} reads an attribute named at run time off an object handed to "
                    "it later, so no exempt chain can be judged")
        segments = full.split(".")
        refused = next((r for end in range(1, len(segments) + 1) for r in REFUSED_REFERENCES
                        if within(".".join(segments[:end]), r)), None)
        if refused is None:
            return _reexport(segments, _called(walk, chain[-1]), spelled)
        if (refused == "inspect" and self._inspect_call(walk, chain, found)) or (
                self._import_call(walk, chain[-1], full)):
            return None
        return f"{spelled} reaches {refused}, which reads where code runs"

    def _inspect_call(self, walk: _Walk, chain: list[ast.AST], found: Imported) -> bool:
        """Whether ``chain`` is ``inspect.<fn>`` called directly, off a name bound to the
        module :mod:`inspect` itself, with at least one argument and every argument an exempt
        chain.  A call with none reads the running frame (``inspect.stack()``,
        ``inspect.currentframe()``), so "every argument is exempt" may not hold vacuously."""
        if found.refers != "inspect" or len(chain) != 2 or not _called(walk, chain[1]):
            return False
        call = walk.parents[id(chain[1])]
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        return bool(arguments) and all(self._exempt(walk, arg) for arg in arguments)

    def _import_call(self, walk: _Walk, callee: ast.AST, dotted: str) -> bool:
        """Whether ``callee``, resolved to ``dotted``, is called directly as an import the
        screen can resolve (:data:`_IMPORT_CALLS`): ``importlib.reload`` of one exempt chain
        (a module another file holds, run again: the same from any file), or
        ``importlib.import_module`` / ``__import__`` of one constant absolute name that
        neither is nor holds the module being split (``__import__``'s ``fromlist`` a list of
        constants; with none it returns the name's top package, which is judged too)."""
        if dotted not in _IMPORT_CALLS or not _called(walk, callee):
            return False
        call = walk.parents[id(callee)]
        if dotted == "importlib.reload":
            return len(call.args) == 1 and not call.keywords and self._exempt(walk, call.args[0])
        if len(call.args) != 1 or not _is_text(call.args[0]):
            return False
        name = call.args[0].value
        names = [name]
        if dotted == "builtins.__import__" and call.keywords:
            entries = call.keywords[0].value
            if (len(call.keywords) != 1 or call.keywords[0].arg != "fromlist"
                    or not isinstance(entries, (ast.List, ast.Tuple)) or not entries.elts
                    or not all(_is_text(entry) for entry in entries.elts)):
                return False
        elif dotted == "builtins.__import__":
            names.append(name.split(".")[0])
        elif call.keywords:
            return False
        return not name.startswith(".") and not any(
            within(each, self.dotted) or within(self.dotted, each) for each in names)

    def _constant_getter(self, walk: _Walk, callee: ast.AST, dotted: str) -> bool:
        """Whether ``callee`` (one of :data:`_NAME_GETTERS`) is called directly with every
        attribute name a string constant (rule 2 then judges each)."""
        if not _called(walk, callee):
            return False
        call = walk.parents[id(callee)]
        names = call.args if dotted == "operator.attrgetter" else call.args[:1]
        return bool(names) and all(_is_text(name) for name in names)

    def _check_by_name(self, walk: _Walk, node: ast.Call) -> str | None:
        """``getattr`` and ``hasattr`` with a name computed at run time, off anything but an
        exempt chain: the screen cannot know which attribute is read."""
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in _BY_NAME
                and self._resolved(walk, func).kind == "builtin" and len(node.args) >= 2):
            return None
        if _is_text(node.args[1]) or self._exempt(walk, node.args[0]):
            return None
        return f"{func.id} with a name computed at run time, off {_spelled(node.args[0])}"

    def _check_request_carrier(self, walk: _Walk, node: ast.Attribute) -> str | None:
        """Rule 5 for ``request``'s carriers: ``request.node`` and its kin only as a chain's
        root or a callee."""
        if node.attr not in REQUEST_CARRIERS or _root_name(node) != "request" or (
                self._import_bound(walk, _root(node))):
            return None
        if _as_root(walk, node):
            return None
        return (f"request...{node.attr} is used as a value, so another name could hold what "
                "the rules keyed on request read")

    def _check_bound_member(self, walk: _Walk, node: ast.Attribute) -> str | None:
        """Rule 5 for what ``self`` or ``cls`` hands out in a split class's members: one of
        the class's methods may only be called (``self.m()``), never handed out or read
        further, because a bound member's ``__self__``, ``__func__`` and qualname name the
        mixin or a slice, so another name would hold what the rules keyed on ``self`` read."""
        if (walk.members is None or node.attr not in walk.members
                or not isinstance(node.value, ast.Name) or node.value.id not in SPLIT_CARRIERS
                or _called(walk, node)):
            return None
        return (f"{_spelled(node)} hands out a method of a split class, whose qualname, "
                "function and owner name the mixin or a slice")

    def _check_reexport(self, walk: _Walk, node: ast.Attribute) -> str | None:
        """A module :data:`_MODULE_NAMED` names, read as an attribute off anything but an
        import-bound name (which :meth:`_reference` judges by what its import binds)."""
        if isinstance(node.value, ast.Attribute) or self._import_bound(walk, node.value):
            return None
        chain = _chain(walk, node)
        return _reexport(["", *(step.attr for step in chain)], _called(walk, chain[-1]),
                         _spelled(chain[-1]))

    def _exempt(self, walk: _Walk, node: ast.AST) -> bool:
        """Rule 3: whether ``node`` is a pure attribute chain off a name bound only by imports
        of something other than the source or its package, traversing into neither."""
        attributes = []
        while isinstance(node, ast.Attribute):
            attributes.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id in self.never_foreign:
            return False
        binding = self._binding(walk, node)
        return binding.kind == "import" and all(
            not found.names(self.dotted)
            and not within(".".join([found.refers, *attributes]), self.dotted)
            for found in binding.imports)

    def _import_bound(self, walk: _Walk, node: ast.AST | None) -> bool:
        """Whether ``node`` is a name bound only by imports where it is read."""
        return isinstance(node, ast.Name) and self._binding(walk, node).kind == "import"

    def _binding(self, walk: _Walk, node: ast.Name) -> _Binding:
        """How the name ``node`` is bound where it is read, resolved by :mod:`symtable`."""
        seen = walk.seen[id(node)]
        if node.id in seen.shadow:
            return _Binding("other")
        found = _binding_scope(seen.scope, node.id)
        if found is None:
            return _Binding("unknown")
        scope, symbol = found
        if symbol is None:
            return self._module_binding(node.id)
        if symbol.is_imported() and not symbol.is_assigned() and not symbol.is_parameter():
            return _Binding("import", self._imports_of(scope.imports, node.id))
        return _Binding("other")

    def _resolved(self, walk: _Walk, node: ast.Name) -> _Binding:
        """How ``node`` is bound for the rules that REFUSE: as :meth:`_binding` says, or,
        where no symbol table could be matched (two lambdas or generator expressions on one
        line; none in the 99 ``tests/`` modules of 1,000+ lines the source reader accepts,
        2026-10-15), by every absolute import of its name anywhere in the source, else as a
        builtin unless a node binds it, so the name is held to every rule it could meet.  The
        exemption never reads this, so an unresolved name is never exempt."""
        binding = self._binding(walk, node)
        if binding.kind != "unknown":
            return binding
        found = self._imports_of(self.statements, node.id)
        if found:
            return _Binding("import", found)
        return _Binding("other" if node.id in self.unit_names else "builtin")

    def _module_binding(self, name: str) -> _Binding:
        """How a module-level name of the source is bound: by its import block, by a node, or
        not at all (a builtin)."""
        found = self._imports_of(self.module_scope.imports, name)
        if found:
            return _Binding("import", found)
        return _Binding("other" if name in self.unit_names else "builtin")

    def _imports_of(self, statements: list[ast.Import | ast.ImportFrom], name: str) -> tuple:
        """What each import statement binding ``name`` imports for it."""
        return tuple(found for node in statements for found in (
            imported(node, alias, self.package) for alias in node.names) if found.bound == name)


def _binding_scope(scope: _Scope | None, name: str
                   ) -> tuple[_Scope, symtable.Symbol | None] | None:
    """The scope binding ``name`` read in ``scope``, with its symbol there: None for the
    symbol when the module binds it (a global, or read at module level), whose bindings the
    source's imports and nodes give; None for both when a table is missing.  A free name is
    looked up in the enclosing function scopes, skipping class bodies as Python does."""
    while scope is not None:
        if scope.table is None:
            return None
        if scope.parent is None:
            return scope, None
        try:
            symbol = scope.table.lookup(name)
        except KeyError:
            return None
        if symbol.is_global() or symbol.is_declared_global():
            return scope, None
        if not symbol.is_free():
            return scope, symbol
        scope = scope.parent
        while scope is not None and scope.is_class:
            scope = scope.parent
    return None


def _reach_patches(nodes: list[ast.AST]) -> set[int]:
    """The ``globals`` names in ``<x>.setitem(globals(), ...)`` / ``.delitem`` calls: the
    reach rule's own shape (the coordinator's ruling on Fork 3 item 6), which
    :func:`tools.split._layout.globals_keys` and the reach rule then hold to their terms."""
    return {id(node.args[0].func) for node in nodes
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"setitem", "delitem"} and node.args
            and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name)
            and node.args[0].func.id == "globals" and not node.args[0].args}


def _root(node: ast.AST) -> ast.AST:
    """The node an attribute, subscript or call chain starts from."""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _root_name(node: ast.AST) -> str | None:
    """The name an attribute, subscript or call chain starts from."""
    root = _root(node)
    return root.id if isinstance(root, ast.Name) else None


def _spelled(node: ast.AST) -> str:
    """``node`` as written, shortened for a refusal message."""
    text = ast.unparse(node)
    return text if len(text) <= 60 else text[:57] + "..."


def _chain(walk: _Walk, start: ast.AST) -> list[ast.AST]:
    """``start`` and every attribute read directly off it in turn: ``a.b.c`` from ``a``."""
    chain = [start]
    while True:
        parent = walk.parents.get(id(chain[-1]))
        if not (isinstance(parent, ast.Attribute) and parent.value is chain[-1]):
            return chain
        chain.append(parent)


def _called(walk: _Walk, node: ast.AST) -> bool:
    """Whether ``node`` is the callee of the call holding it."""
    parent = walk.parents.get(id(node))
    return isinstance(parent, ast.Call) and parent.func is node


def _as_root(walk: _Walk, node: ast.AST) -> bool:
    """Whether ``node`` is the root of an attribute read or the callee of a call."""
    parent = walk.parents.get(id(node))
    return (isinstance(parent, ast.Attribute) and parent.value is node) or _called(walk, node)


def _is_text(node: ast.AST) -> bool:
    """Whether ``node`` is a string constant."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _reexport(segments: list[str], called: bool, spelled: str) -> str | None:
    """Why a chain whose dotted name holds a module :data:`_MODULE_NAMED` names as another's
    attribute (``os.sys.modules``) could reach where code runs, or None.  ``segments[0]`` is
    the chain's root, judged elsewhere.  A segment called directly is a function of that
    name (SQLAlchemy's ``inspect``), since a module cannot be called; one used as a value
    may be the module, which no other name may hold."""
    for at in range(1, len(segments)):
        if segments[at] not in _MODULE_NAMED:
            continue
        if at == len(segments) - 1:
            return None if called else (f"{spelled} may be the module {segments[at]} held "
                                        "by another, used as a value")
        rest = segments[at:]
        refused = next((r for r in REFUSED_REFERENCES for end in range(1, len(rest) + 1)
                        if within(".".join(rest[:end]), r)), None)
        if refused is not None:
            return f"{spelled} may reach {refused} through another module that holds it"
    return None


#: Decorators whose function is read as a value, not handed out as a bound member.
_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "setter", "getter",
                                  "deleter"})


def _methods(node: ast.ClassDef) -> frozenset[str]:
    """The methods ``node`` defines, a property's name excepted (reading one hands out the
    value it computes, not a bound member)."""
    functions = [member for member in node.body
                 if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))]
    properties = {member.name for member in functions for decorator in member.decorator_list
                  if _last_name(decorator) in _PROPERTY_DECORATORS}
    return frozenset(member.name for member in functions) - properties


def _last_name(node: ast.AST) -> str | None:
    """The last name a decorator is spelled with: ``property``, ``x.setter``, ``f(...)``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None
