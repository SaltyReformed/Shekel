"""A source test module read as the split tool moves it: its docstring, its import block, and
its top-level nodes, each as a BLOCK of source lines that carries its leading comments.

**A block is the unit of a move.**  Its lines run from the first non-blank line after the
previous node (or after the import block) through the node's last line, so a section banner
or a comment above a test travels with the node below it and no comment is left without a
home.  The writer copies a block's lines verbatim and the prover compares them verbatim; an
``ast.dump`` comparison alone cannot see a dropped comment.

**What this module refuses to read**, because a move could change its meaning or lose it:
a line before the module docstring; a comment anywhere in the import block (it has no node
to travel with); an import after the first non-import statement; a relative or star import;
a module-level statement other than an import, a ``def``, a ``class`` or a single-name
assignment; a name bound twice at module level; anything after the last node.
"""
from __future__ import annotations

import ast
import configparser
import fnmatch
import io
import json
import symtable
import tokenize
import types
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tools.ci import text as ci_text


class Refusal(RuntimeError):
    """The tool cannot PROVE a move of this source under this plan, so it makes none."""


# The ONE reader.  Every file's text, every process's output, every JSON value, syntax tree,
# symbol table and ini file the tool reads goes through the functions below, and each refuses,
# naming what it read, where Python would raise (the coordinator's ruling on the delta
# review's L-B, 2026-10-15: every path from ``split.main`` to a traceback becomes a refusal
# naming the file).  An OSError is not converted here: it names its file itself, and
# ``split.main`` reports it, the one place it is caught.  The translation of line endings
# and the splitter of text into lines this reader uses live in :mod:`tools.ci.text`, the
# layer below this package and :mod:`tools.testrecord` both (the coordinator's ruling on the
# lane review's M-B, 2026-10-17).


def undecodable(exc: UnicodeDecodeError, where: str, separator: bytes = b"\n") -> Refusal:
    """The refusal for bytes read from ``where`` that are not UTF-8: it names the byte and
    shows the record holding it, the bytes between two ``separator``\\ s (a file's line; one
    path of ``git ls-files -z``), so the file is found even when ``where`` is a process."""
    data = exc.object
    start = data.rfind(separator, 0, exc.start) + 1
    end = data.find(separator, exc.start)
    record = data[start:end if end >= 0 else len(data)]
    return Refusal(f"{where}: not UTF-8 text ({exc.reason} at byte {exc.start}, in "
                   f"{record[:160]!r})")


def decode(data: bytes, where: str, separator: bytes = b"\n") -> str:
    """``data`` decoded strictly as UTF-8, the ONE decoder of what the tool reads as text;
    refused (:func:`undecodable`) when it is not UTF-8.  Nothing is translated: a carriage
    return is kept, for the readers that refuse one."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise undecodable(exc, where, separator) from exc


def text_from(data: bytes, where: str) -> str:
    """Bytes read from ``where`` as text, as :meth:`pathlib.Path.read_text` reads a file's:
    decoded by :func:`decode`, newlines translated (:func:`tools.ci.text.universal`).  For a
    reader that keeps the bytes it parsed (:func:`tools.split._model.parse_plan` hashes them)."""
    return ci_text.universal(decode(data, where)).read()


def read_text(path: Path) -> str:
    """A file's text, by :func:`text_from`."""
    return text_from(path.read_bytes(), str(path))


def text_holding(path: Path, needle: str) -> str | None:
    """A file's text, untranslated, when its bytes hold ``needle``; None, and nothing
    decoded, when they do not.  A walk over every ``.py`` of the tree for an import of one
    module therefore never refuses a file that does not name it (the delta review's L-B:
    the importer walk decoded every file before its filter, so a stray non-UTF-8 ``.py``
    anywhere stopped ``record``).  UTF-8 is self-synchronising, so the bytes hold the
    needle's bytes exactly when the text holds the needle."""
    data = path.read_bytes()
    return decode(data, str(path)) if needle.encode("utf-8") in data else None


def load_json(text: str, where: str) -> object:
    """``text``'s JSON value; refused, naming ``where``, when it is not JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal(f"{where}: not JSON ({exc})") from exc


def read_json(path: Path) -> object:
    """A JSON file's value, by :func:`read_text` and :func:`load_json`."""
    return load_json(read_text(path), str(path))


def _not_compiled(exc: SyntaxError, where: str) -> Refusal:
    """The refusal of ``where``, which Python does not compile."""
    return Refusal(f"{where}: Python does not compile it ({exc.msg}, line {exc.lineno})")


def compiled(text: str, where: str) -> types.CodeType:
    """``text`` as Python compiles a module, refused, naming ``where``, unless it compiles: a
    syntax error, a null byte, or an error only the compiler's symbol pass finds (``return``
    outside a function, a name both a parameter and ``global``)."""
    try:
        return compile(text, where, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise _not_compiled(exc, where) from exc


def parse(text: str, where: str) -> ast.Module:
    """``text``'s syntax tree, refused, naming ``where``, unless Python compiles it
    (:func:`compiled`), which :mod:`symtable` would otherwise raise inside the layout; text
    the compiler accepts, :func:`ast.parse` (the same compiler, stopped at the tree) accepts
    too."""
    compiled(text, where)
    return ast.parse(text, filename=where)


def symbols(text: str, where: str) -> symtable.SymbolTable:
    """``text``'s symbol table; refused, naming ``where``, when Python does not compile it."""
    try:
        return symtable.symtable(text, where, "exec")
    except SyntaxError as exc:
        raise _not_compiled(exc, where) from exc


def read_ini(path: Path) -> configparser.ConfigParser:
    """An ini file the repository configures a tool by (``pytest.ini``, ``.pylintrc``), as
    :mod:`configparser` reads it; refused, naming the file, when it is absent or unreadable
    (:mod:`configparser` skips a file it cannot open), not UTF-8, or not one
    :mod:`configparser` reads."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        found = parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as exc:
        raise Refusal(f"{path}: not an ini file configparser reads ({exc})") from exc
    if not found:
        raise Refusal(f"no {path} to read")
    return parser


class Kind(Enum):
    """What a top-level node is to pytest, which decides where it may go."""

    TEST = "test"
    FIXTURE = "fixture"
    HELPER = "helper"


@dataclass(frozen=True)
class CollectRules:
    """``pytest.ini``'s ``python_files`` / ``python_classes`` / ``python_functions`` globs,
    read from the repository rather than restated here."""

    files: tuple[str, ...]
    classes: tuple[str, ...]
    functions: tuple[str, ...]

    def is_test_file(self, name: str) -> bool:
        """Whether pytest collects a module file called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.files)

    def is_test_class(self, name: str) -> bool:
        """Whether pytest collects a class called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.classes)

    def is_test_function(self, name: str) -> bool:
        """Whether pytest collects a function or method called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.functions)

    def is_test_name(self, name: str) -> bool:
        """Whether a name pytest would collect if a module bound it."""
        return self.is_test_class(name) or self.is_test_function(name)


def collect_rules(root: Path) -> CollectRules:
    """Read the collection globs from ``<root>/pytest.ini``; refuse when it holds none."""
    parser = read_ini(root / "pytest.ini")
    if "pytest" not in parser:
        raise Refusal(f"no [pytest] section in {root / 'pytest.ini'}")
    section = parser["pytest"]
    try:
        return CollectRules(
            files=tuple(section["python_files"].split()),
            classes=tuple(section["python_classes"].split()),
            functions=tuple(section["python_functions"].split()),
        )
    except KeyError as exc:
        raise Refusal(f"pytest.ini names no {exc.args[0]}; the tool will not assume one") from exc


@dataclass(frozen=True)
class Block:
    """One node and the lines it moves with: ``first`` is its first leading comment (or the
    node's own first line), ``start`` the node's first line counting decorators."""

    name: str
    node: ast.stmt
    first: int
    start: int
    end: int

    def text(self, lines: tuple[str, ...]) -> tuple[str, ...]:
        """The block's lines, verbatim."""
        return lines[self.first - 1:self.end]


@dataclass(frozen=True)
class Unit:
    """A top-level block and what it is to pytest."""

    block: Block
    kind: Kind

    @property
    def name(self) -> str:
        """The name the node binds at module level."""
        return self.block.name


@dataclass(frozen=True)
class ImportStmt:
    """One top-level import statement, the names it binds, and its blank-line group."""

    node: ast.Import | ast.ImportFrom
    group: int

    @property
    def is_future(self) -> bool:
        """Whether this is a ``from __future__`` import, which every output repeats."""
        return isinstance(self.node, ast.ImportFrom) and self.node.module == "__future__"

    def bindings(self) -> dict[str, ast.alias]:
        """Each module-level name this statement binds, to the alias that binds it."""
        return {bound_name(alias, self.node): alias for alias in self.node.names}


@dataclass(frozen=True)
class SourceModule:
    """A parsed source module: its lines, docstring end, imports and top-level units."""

    path: Path
    lines: tuple[str, ...]
    docstring_end: int
    imports: tuple[ImportStmt, ...]
    units: tuple[Unit, ...]

    def unit(self, name: str) -> Unit | None:
        """The top-level unit binding ``name``, if any."""
        return next((u for u in self.units if u.name == name), None)

    def import_bindings(self) -> dict[str, ImportStmt]:
        """Every name the import block binds, to the statement binding it."""
        return {name: stmt for stmt in self.imports if not stmt.is_future
                for name in stmt.bindings()}


def bound_name(alias: ast.alias, node: ast.Import | ast.ImportFrom) -> str:
    """The module-level name an import alias binds (``import a.b`` binds ``a``)."""
    if alias.asname:
        return alias.asname
    return alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name


def within(dotted: str, module: str) -> bool:
    """Whether the dotted name ``dotted`` is ``module`` or lies inside it."""
    return dotted == module or dotted.startswith(module + ".")


@dataclass(frozen=True)
class Imported:
    """What one alias of an import statement does, as dotted names: ``bound``, the name it
    binds; ``refers``, what that name then refers to (``import a.b`` binds ``a``, the package
    ``a``; ``import a.b as x`` binds ``x``, the module ``a.b``); and ``imports``, every module
    or object the statement imports for it (``a.b``; or ``m`` and ``m.n`` for
    ``from m import n``)."""

    bound: str
    refers: str
    imports: tuple[str, ...]

    def names(self, module: str) -> bool:
        """Whether this alias imports ``module`` or anything inside it."""
        return any(within(dotted, module) for dotted in self.imports)


def imported(node: ast.Import | ast.ImportFrom, alias: ast.alias, package: str) -> Imported:
    """What ``alias`` of ``node`` imports, a relative import resolved against ``package``,
    the package of the module the statement sits in (its ``__package__``).

    The one producer of "this import names the source" (the coordinator's ruling on the
    delta review's L-C, 2026-10-15): the writer re-pointing importers and the identity screen
    both ask it, so they cannot disagree about a relative spelling again.  Refused when the
    dots climb above the top package, which Python refuses too."""
    if isinstance(node, ast.Import):
        refers = alias.name if alias.asname else alias.name.split(".")[0]
        return Imported(bound_name(alias, node), refers, (alias.name,))
    base = node.module or ""
    if node.level:
        parts = package.split(".") if package else []
        if node.level > len(parts):
            raise Refusal(f"line {node.lineno}: a relative import climbs above the top package")
        base = ".".join(parts[:len(parts) - (node.level - 1)] + ([base] if base else []))
    full = f"{base}.{alias.name}" if base else alias.name
    return Imported(bound_name(alias, node), full, (base, full) if base else (full,))


def is_docstring(node: ast.stmt) -> bool:
    """Whether ``node`` is a string-literal expression statement."""
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def attribute_read(node: ast.AST) -> tuple[ast.expr, str] | None:
    """``(object, attribute)`` when ``node`` reaches a named attribute: ``obj.attr``, or
    ``getattr(obj, "attr")`` with a literal name; else None."""
    if isinstance(node, ast.Attribute):
        return node.value, node.attr
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2):
        return None
    name = node.args[1]
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        return node.args[0], name.value
    return None


def fixture_decorator(node: ast.stmt) -> ast.expr | None:
    """The ``@pytest.fixture`` / ``@fixture`` decorator on a function, or None."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (isinstance(target, ast.Attribute) and target.attr == "fixture"
                and isinstance(target.value, ast.Name) and target.value.id == "pytest"):
            return decorator
        if isinstance(target, ast.Name) and target.id == "fixture":
            return decorator
    return None


def fixture_scope(decorator: ast.expr) -> str | None:
    """A fixture decorator's scope, ``"function"`` when unstated; None when it cannot be
    read statically (a positional argument or a computed scope)."""
    if not isinstance(decorator, ast.Call):
        return "function"
    if decorator.args:
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "scope":
            value = keyword.value
            return value.value if isinstance(value, ast.Constant) and isinstance(
                value.value, str) else None
    return "function"


def _comment_lines(text: str) -> frozenset[int]:
    """The line of every comment token in ``text``."""
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return frozenset(tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT)


def _binding_name(node: ast.stmt, where: str) -> str:
    """The one name a top-level node binds, or a refusal for any other statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        raise Refusal(f"{where}: an import after the first non-import statement")
    raise Refusal(f"{where}: a module-level {type(node).__name__} has no single name to "
                  "move by, and running it elsewhere could change its effect")


def blocks(nodes: list[ast.stmt], after: int, lines: tuple[str, ...], where: str
           ) -> list[Block]:
    """Cut ``nodes`` (siblings, in order) into blocks, the first starting after line
    ``after``; each block takes the non-blank lines above it back to its predecessor."""
    found = []
    previous_end = after
    for node in nodes:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        first = next((n for n in range(previous_end + 1, start) if lines[n - 1].strip()),
                     start)
        name = _binding_name(node, f"{where}:{node.lineno}")
        found.append(Block(name=name, node=node, first=first, start=start,
                           end=node.end_lineno))
        previous_end = node.end_lineno
    return found


def _kind(node: ast.stmt, rules: CollectRules) -> Kind:
    """Whether pytest collects ``node``, runs it as a fixture, or neither."""
    if isinstance(node, ast.ClassDef) and rules.is_test_class(node.name):
        return Kind.TEST
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if rules.is_test_function(node.name):
            return Kind.TEST
        if fixture_decorator(node) is not None:
            return Kind.FIXTURE
    return Kind.HELPER


def _imports(body: list[ast.stmt], lines: tuple[str, ...], where: str,
             relative: str | None) -> list[ImportStmt]:
    """Pop the leading import statements off ``body``, each with its blank-line group; the
    one relative import allowed is ``from .<relative> import ...``."""
    found: list[ImportStmt] = []
    group = 0
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        node = body.pop(0)
        if isinstance(node, ast.ImportFrom) and not (
                relative is not None and node.level == 1 and node.module == relative) and (
                node.level or [a.name for a in node.names] == ["*"]):
            raise Refusal(f"{where}:{node.lineno}: a relative or star import cannot be "
                          "re-derived for a part one directory deeper")
        if found and not lines[found[-1].node.end_lineno].strip():
            group += 1
        found.append(ImportStmt(node=node, group=group))
    return found


def _check_header(lines: tuple[str, ...], text: str, docstring_end: int, header_end: int,
                  where: str) -> None:
    """Refuse a comment in the import block: it has no node to travel with."""
    stray = sorted(n for n in _comment_lines(text) if docstring_end < n <= header_end)
    if stray:
        raise Refusal(f"{where}: comment line(s) {stray} in the import block would be lost; "
                      f"first: {lines[stray[0] - 1].strip()!r}")


def _check_bindings(imports: list[ImportStmt], found: list[Block], where: str) -> None:
    """Refuse a name bound twice at module level: which binding a part sees would move."""
    names = [n for stmt in imports for n in stmt.bindings()] + [b.name for b in found]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise Refusal(f"{where}: names bound twice at module level: {twice}")


def read_module(path: Path, rules: CollectRules, relative: str | None = None) -> SourceModule:
    """Read ``path`` into a :class:`SourceModule`, refusing what a move could not keep.

    ``relative`` names the one sibling module a relative import may read: none for a
    source, ``_helpers`` for an output the tool wrote.
    """
    text = read_text(path)
    lines = tuple(ci_text.lines_of(text))
    where = str(path)
    body = list(parse(text, where).body)
    docstring_end = 0
    if body and is_docstring(body[0]):
        if body[0].lineno != 1:
            raise Refusal(f"{where}: lines before the module docstring have no home")
        docstring_end = body.pop(0).end_lineno
    imports = _imports(body, lines, where, relative)
    header_end = imports[-1].node.end_lineno if imports else docstring_end
    _check_header(lines, text, docstring_end, header_end, where)
    found = blocks(body, header_end, lines, where)
    last = found[-1].end if found else header_end
    if any(line.strip() for line in lines[last:]):
        raise Refusal(f"{where}: lines after the last node have no node to travel with")
    _check_bindings(imports, found, where)
    units = tuple(Unit(block=b, kind=_kind(b.node, rules)) for b in found)
    return SourceModule(path=path, lines=lines, docstring_end=docstring_end,
                        imports=tuple(imports), units=units)


def _table_globals(table: symtable.SymbolTable) -> set[str]:
    """Every global name ``table`` or a scope nested in it reads."""
    found = {s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
    for child in table.get_children():
        found |= _table_globals(child)
    return found


def free_names(text: str) -> frozenset[str]:
    """The module-level names a block of top-level source reads and does not bind itself.

    Resolved by :mod:`symtable`, so a local, a parameter or a comprehension variable that
    shares a module-level name is NOT counted: the scopes are Python's, not a guess.
    """
    top = symbols(text, "<block>")
    found = {s.get_name() for s in top.get_symbols()
             if s.is_referenced() and not s.is_assigned() and not s.is_imported()}
    for child in top.get_children():
        found |= _table_globals(child)
    return frozenset(found)


#: The stand-in class a class member's lines are read inside, so the class scope is Python's
#: own.
_PROBE = "_SplitProbe"


def _member_probe(member_lines: tuple[str, ...]) -> str:
    """A module holding only the stand-in class, its body ``member_lines``."""
    return f"class {_PROBE}:\n" + "\n".join(member_lines) + "\n"


def member_free_names(member_lines: tuple[str, ...]) -> frozenset[str]:
    """:func:`free_names` for a class member's lines, read inside a stand-in class so the
    class scope is Python's own (a method does not see a class attribute by bare name)."""
    top = symbols(_member_probe(member_lines), "<member>")
    found: set[str] = set()
    for child in top.get_children():
        found |= _table_globals(child)
    return frozenset(found)


def member_private_names(member_lines: tuple[str, ...]) -> frozenset[str]:
    """Every identifier in a class member's lines that the compiler spells by the ENCLOSING
    class's name: one with two leading underscores and not two trailing, ``__x`` written as
    ``_<Class>__x``, so a split, which puts a member on the mixin and a test on a slice,
    spells it apart on each side (the coordinator's ruling on 3C-3b's report, 2026-10-16).

    Read from the code the compiler writes for the member inside a stand-in class (the
    names, locals, cells and free variables of every code object), so the rule is the
    compiler's own and no node kind is listed: an attribute (``self.__x``), a name, a
    parameter, a local, a nested function's or class's name.  A call's keyword name
    (``f(__kw=1)``) is not mangled, nor is a string: ``getattr(self, "__x")`` reads the
    attribute ``__x`` as spelled on both sides of a split, and a name computed at run time
    is refused by :mod:`tools.split._identity`'s by-name rule."""
    prefix = f"_{_PROBE.lstrip('_')}__"
    found: set[str] = set()
    pending = [compiled(_member_probe(member_lines), "<member>")]
    while pending:
        code = pending.pop()
        found |= {"__" + name[len(prefix):] for name in (
            *code.co_names, *code.co_varnames, *code.co_cellvars, *code.co_freevars)
                  if name.startswith(prefix)}
        pending.extend(const for const in code.co_consts if isinstance(const, types.CodeType))
    return frozenset(found)
