"""The STATIC half of the proof that a split is a pure move, read off the files on disk.

The prover never trusts the writer.  It re-reads every output with the same strict reader
the source went through, then holds each block against the source's:

* **Identity.**  A whole node's block (its leading comments included) is the source's
  block line for line, and its ``ast.dump`` is identical.  A split class is proved per
  MEMBER: each test in a slice, and each non-test member and the docstring on the mixin,
  is the source's member line for line and dump for dump; the generated ``class`` line and
  one-line docstring must have exactly the shape the layout prescribes.
* **Conservation.**  Every top-level node, and every member of a split class, appears
  exactly once across the outputs: a dropped test and a duplicated one both fail.  Because
  the reader refuses a comment in an import block and anything after the last node, and
  every block's leading lines are compared verbatim, no non-blank line of an output goes
  unchecked and no comment of the source is lost.
* **Order.**  Within each output, nodes (and a slice's members) keep source order.
* **Placement.**  Fixtures are in ``conftest.py`` and nothing else is; tests are in parts.
* **Imports.**  Each import is a source statement filtered to fewer aliases, the
  ``__future__`` import, or a ``from ._helpers`` import of helpers; none binds a name pytest
  would collect.  Each output's free names resolve in it, it imports nothing it does not
  read (beyond the plan's placed unused imports), and every source alias lands somewhere.
  The ``__future__`` statements are a directive, not names to read: each output carries
  exactly the source's.
* **Size.**  Every file is under the line budget.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from tools.split._classes import ClassShape, mixin_name
from tools.split._layout import BUILTINS, Layout, Output
from tools.split._model import CONFTEST, HELPERS, INIT
from tools.split._render import (
    IMPORTER_ROOTS,
    LINE_BUDGET,
    Origin,
    importer_rewrites,
    line_limit,
    rendered,
    text_of,
)
from tools.split._source import (
    Block,
    CollectRules,
    ImportStmt,
    Kind,
    Refusal,
    SourceModule,
    Unit,
    blocks,
    free_names,
    is_docstring,
    member_free_names,
    read_module,
)


class _Tally:
    """What the outputs held, counted across all of them, and every failure found."""

    def __init__(self, source_lines: tuple[str, ...]) -> None:
        """Start an empty tally over the source's lines."""
        self.source_lines = source_lines
        self.units: Counter[str] = Counter()
        self.members: Counter[tuple[str, str]] = Counter()
        self.aliases: set[str] = set()
        self.failures: list[str] = []

    def fail(self, message: str) -> None:
        """Record one failure."""
        self.failures.append(message)


def _same_block(tally: _Tally, where: str, out: tuple[tuple[str, ...], ast.AST],
                orig: tuple[tuple[str, ...], ast.AST]) -> None:
    """Fail unless ``out`` -- a block's ``(lines, node)`` -- is ``orig`` line for line and
    dump for dump."""
    (got, got_node), (want, want_node) = out, orig
    if got != want:
        diff = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w),
                    min(len(got), len(want)))
        tally.fail(f"{where}: lines differ from the source's at block line {diff + 1} "
                   f"({len(got)} vs {len(want)} lines)")
    if ast.dump(got_node) != ast.dump(want_node):
        tally.fail(f"{where}: ast.dump differs from the source's")


def _futures(imports: tuple[ImportStmt, ...]) -> list[tuple[tuple[str, str | None], ...]]:
    """Each ``from __future__`` statement among ``imports``, as its features in order."""
    return [tuple((a.name, a.asname) for a in stmt.node.names)
            for stmt in imports if stmt.is_future]


def _check_imports(tally: _Tally, layout: Layout, module: str,
                   imports: tuple[ImportStmt, ...], rules: CollectRules) -> set[str]:
    """Fail any import that is not a filtered source import, the future import, or a
    helpers import, and an output that does not carry exactly the source's ``__future__``
    statements; return the names the output's imports bind for its code to read.

    A ``__future__`` statement is a DIRECTIVE to the compiler, not a name an output's code
    reads: the writer repeats it in every output whether or not anything there reads
    ``annotations``, so its names are left out of what this returns, and an output that
    reads none of them is not importing something unread.  It is CONSERVED instead: each
    output must carry exactly the source's ``__future__`` statements, a fact this proves
    rather than trusts the writer for, since an output that dropped one would evaluate its
    annotations differently and a test's outcome need not show it."""
    sources = layout.source.imports
    if _futures(imports) != _futures(sources):
        tally.fail(f"{module}: carries the __future__ statements {_futures(imports)}, not the "
                   f"source's {_futures(sources)}")
    bound: set[str] = set()
    for stmt in imports:
        node = stmt.node
        names = {} if stmt.is_future else stmt.bindings()
        bound |= set(names)
        for name in names:
            if rules.is_test_name(name):
                tally.fail(f"{module}: imports {name}, which pytest would collect again")
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == HELPERS:
            if module == HELPERS:
                tally.fail(f"{module}: imports from itself")
            for name, alias in names.items():
                if alias.asname or layout.home.get(name) != HELPERS:
                    tally.fail(f"{module}: imports {name} from _helpers, which is not homed there")
            continue
        aliases = {(a.name, a.asname) for a in node.names}
        if not any(type(s.node) is type(node) and getattr(s.node, "module", None) == getattr(
                node, "module", None) and getattr(s.node, "level", 0) == getattr(node, "level", 0)
                and aliases <= {(a.name, a.asname) for a in s.node.names} for s in sources):
            tally.fail(f"{module}: import at line {node.lineno} is not a source import")
        tally.aliases |= set(names)
    return bound


def _name_failures(module: str, needs: set[str], bound: set[str], imported: set[str],
                   placed: set[str]) -> list[str]:
    """An unresolved free name, or an import the output does not read (beyond the plan's
    placed unused imports ``placed``)."""
    failures = [f"{module}: reads {name}, bound nowhere in it"
                for name in sorted(needs - bound - imported) if name not in BUILTINS]
    failures += [f"{module}: imports {name} and never reads it"
                 for name in sorted(imported - needs - placed)]
    return failures


def _check_slice(tally: _Tally, layout: Layout, module: str, unit: Unit,
                 out_lines: tuple[str, ...]) -> set[str]:
    """Prove one slice of a split class; return the names its members read."""
    shape = layout.split[unit.name]
    src_lines = layout.source.lines
    node = unit.block.node
    piece = next((p for p in layout.outputs[module].pieces
                  if p.slice and p.slice.class_name == unit.name), None)
    run = piece.slice if piece else None
    base = mixin_name(unit.name)
    header_ok = (isinstance(node, ast.ClassDef) and not node.decorator_list
                 and not node.keywords and len(node.bases) == 1
                 and isinstance(node.bases[0], ast.Name) and node.bases[0].id == base
                 and node.body and is_docstring(node.body[0]) and run is not None
                 and out_lines[node.lineno - 1] == f"class {unit.name}({base}):"
                 and out_lines[node.body[0].lineno - 1] == f'    """{run.docstring}"""'
                 and node.body[0].lineno == node.body[0].end_lineno == node.lineno + 1)
    if not header_ok:
        tally.fail(f"{module}: {unit.name} is not the slice shape the plan prescribes")
        return set()
    lead = out_lines[unit.block.first - 1:unit.block.start - 1]
    orig = shape.unit.block
    if lead != (src_lines[orig.first - 1:orig.start - 1] if piece.first_slice else ()):
        tally.fail(f"{module}: {unit.name}'s leading lines are not the source class's")
    members = blocks(node.body[1:], node.body[0].end_lineno, out_lines, module)
    if [m.name for m in members] != list(run.tests):
        tally.fail(f"{module}: {unit.name} holds {[m.name for m in members]}, "
                   f"not its slice {list(run.tests)}")
    return _check_members(tally, module, shape, members, out_lines) | {base}


def _check_mixin(tally: _Tally, layout: Layout, unit: Unit, out_lines: tuple[str, ...]
                 ) -> set[str]:
    """Prove a split class's mixin: the class's docstring verbatim and every non-test
    member; return the names its members read."""
    shape = next(s for s in layout.split.values() if mixin_name(s.name) == unit.name)
    node = unit.block.node
    src_lines = layout.source.lines
    start, end = shape.docstring
    shaped = (isinstance(node, ast.ClassDef) and not node.decorator_list and not node.bases
              and not node.keywords and node.body and is_docstring(node.body[0])
              and out_lines[node.lineno - 1] == f"class {unit.name}:"
              and unit.block.first == unit.block.start)
    if not shaped or out_lines[node.body[0].lineno - 1:node.body[0].end_lineno] != (
            src_lines[start - 1:end]):
        tally.fail(f"{HELPERS}: {unit.name} is not the class's docstring and members")
        return set()
    members = blocks(node.body[1:], node.body[0].end_lineno, out_lines, HELPERS)
    if [m.name for m in members] != [m.name for m in shape.non_tests()]:
        tally.fail(f"{HELPERS}: {unit.name} holds {[m.name for m in members]}, not the "
                   f"class's non-test members")
    return _check_members(tally, HELPERS, shape, members, out_lines)


def _check_members(tally: _Tally, module: str, shape: ClassShape, members: list[Block],
                   out_lines: tuple[str, ...]) -> set[str]:
    """Hold each member against the source class's member of that name, in order."""
    by_name = {m.name: (i, m) for i, m in enumerate(shape.members)}
    needs: set[str] = set()
    last = -1
    for member in members:
        if member.name not in by_name:
            tally.fail(f"{module}: {shape.name}.{member.name} is not in the source class")
            continue
        index, orig = by_name[member.name]
        if index <= last:
            tally.fail(f"{module}: {shape.name}.{member.name} is out of source order")
        last = index
        _same_block(tally, f"{module}: {shape.name}.{member.name}",
                    (member.text(out_lines), member.node),
                    (orig.text(tally.source_lines), orig.node))
        tally.members[(shape.name, member.name)] += 1
        needs |= member_free_names(member.text(out_lines))
    return needs


def _placement(tally: _Tally, layout: Layout, module: str, orig: Unit) -> None:
    """Fail a node in an output its kind may not live in, or not where the plan put it."""
    is_part = module not in (CONFTEST, HELPERS)
    if (orig.kind is Kind.FIXTURE) != (module == CONFTEST) or (
            orig.kind is Kind.TEST and not is_part):
        tally.fail(f"{module}: holds {orig.kind.value} {orig.name}")
    if layout.home.get(orig.name) != module:
        tally.fail(f"{module}: holds {orig.name}, which the plan places in "
                   f"{layout.home.get(orig.name)}")


def _check_units(tally: _Tally, layout: Layout, module: str, mod: SourceModule) -> set[str]:
    """Prove each top-level unit of one output; return the names they read."""
    needs: set[str] = set()
    last = -1
    order = {u.name: i for i, u in enumerate(layout.source.units)}
    mixins = {mixin_name(n): n for n in layout.split}
    for unit in mod.units:
        if unit.name in layout.split and module != HELPERS:
            needs |= _check_slice(tally, layout, module, unit, mod.lines)
            index = order[unit.name]
        elif module == HELPERS and unit.name in mixins:
            needs |= _check_mixin(tally, layout, unit, mod.lines)
            index = order[mixins[unit.name]]
        else:
            orig = layout.source.unit(unit.name)
            if orig is None:
                tally.fail(f"{module}: {unit.name} is not in the source")
                continue
            _same_block(tally, f"{module}: {unit.name}",
                        (unit.block.text(mod.lines), unit.block.node),
                        (orig.block.text(layout.source.lines), orig.block.node))
            _placement(tally, layout, module, orig)
            tally.units[unit.name] += 1
            index = order[unit.name]
            needs |= free_names("\n".join(unit.block.text(mod.lines)) + "\n")
        if index <= last:
            tally.fail(f"{module}: {unit.name} is out of source order")
        last = index
    return needs


def _check_output(tally: _Tally, layout: Layout, output: Output, path: Path,
                  rules: CollectRules) -> None:
    """Prove one output file."""
    module = output.module
    try:
        mod = read_module(path, rules, relative=HELPERS)
    except (Refusal, SyntaxError) as exc:
        tally.fail(f"{module}: unreadable ({exc})")
        return
    if len(mod.lines) >= LINE_BUDGET:
        tally.fail(f"{module}: {len(mod.lines)} lines, over the {LINE_BUDGET}-line budget")
    docstring = ast.get_docstring(ast.parse("\n".join(mod.lines)), clean=False) or ""
    if docstring.split() != output.docstring.split():
        tally.fail(f"{module}: its docstring is not the one the plan gives it")
    imported = _check_imports(tally, layout, module, mod.imports, rules)
    needs = _check_units(tally, layout, module, mod)
    placed = {a for a, m in layout.plan.unused_imports.items() if m == module}
    for failure in _name_failures(module, needs, {u.name for u in mod.units}, imported, placed):
        tally.fail(failure)


def _check_conservation(tally: _Tally, layout: Layout) -> None:
    """Fail a source node or split-class member held zero times, or more than once."""
    for unit in layout.source.units:
        if unit.name in layout.split:
            for member in layout.split[unit.name].members:
                count = tally.members[(unit.name, member.name)]
                if count != 1:
                    tally.fail(f"{unit.name}.{member.name} is held {count} times")
        elif tally.units[unit.name] != 1:
            tally.fail(f"{unit.name} is held {tally.units[unit.name]} times")
    lost = sorted(set(layout.source.import_bindings()) - tally.aliases)
    if lost:
        tally.fail(f"source imports held by no output: {lost}")


def _imports_of(path: Path, target: str) -> list[tuple[int, str]]:
    """``(line, name)`` for each name ``path`` imports from module ``target``."""
    text = path.read_text(encoding="utf-8")
    if target not in text:
        return []
    return [(node.lineno, alias.name) for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.ImportFrom) and node.module == target
            for alias in node.names]


def _check_importers(tally: _Tally, layout: Layout, root: Path) -> None:
    """Fail an import of the source left un-rewritten, or a rewritten one that names a
    name ``_helpers.py`` does not hold."""
    try:
        left = importer_rewrites(layout, root)
    except Refusal as exc:
        tally.fail(f"importers: {exc}")
        return
    for rewrite in left:
        tally.fail(f"{rewrite.path}:{rewrite.line}: still imports the source module")
    target = f"{layout.plan.dotted}.{HELPERS}"
    for top in IMPORTER_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            for line, name in _imports_of(path, target):
                if layout.home.get(name) != HELPERS:
                    tally.fail(f"{path}:{line}: imports {name} from _helpers, which does "
                               "not hold it")


def prove_static(layout: Layout, root: Path, rules: CollectRules) -> list[str]:
    """Every failure of the static proof for ``layout`` as written under ``root``."""
    tally = _Tally(layout.source.lines)
    package = root / layout.plan.package
    if (root / layout.plan.source).exists():
        tally.fail(f"{layout.plan.source} still exists")
    expected = {f"{INIT}.py"} | {f"{m}.py" for m in layout.outputs}
    present = {p.name for p in package.glob("*.py")}
    if present != expected:
        tally.fail(f"{package}: holds {sorted(present)}, expected {sorted(expected)}")
        return tally.failures
    docstring = layout.source.lines[:layout.source.docstring_end]
    if (package / f"{INIT}.py").read_text(encoding="utf-8") != "\n".join(docstring) + "\n":
        tally.fail(f"{INIT}.py is not the source's module docstring verbatim")
    for output in layout.outputs.values():
        _check_output(tally, layout, output, package / f"{output.module}.py", rules)
    _check_conservation(tally, layout)
    _check_importers(tally, layout, root)
    return tally.failures


def line_origins(layout: Layout, root: Path
                 ) -> tuple[dict[str, list[Origin | None]], list[str]]:
    """Each written file's line origins, by repository-relative path, for judging pylint's
    messages by origin; and a failure for every claim the files do not bear out.

    The prover takes no origin on the writer's word: a file on disk must be exactly what the
    writer renders from the recorded source, each line it calls copied must be its source
    line verbatim, and each line it calls an import must lie on a source import statement's
    own lines.  And no import the writer composed (the ``_helpers`` import) may sit before a
    source-derived one, read off the file's own syntax tree: the pylint arm's reading of an
    import-sequence message (:func:`tools.split._dynamic.compare_pylint`) holds only for a
    part whose source-derived imports run unbroken (the coordinator's ruling, 2026-10-15)."""
    package = root / layout.plan.package
    spans = {(s.node.lineno, s.node.end_lineno) for s in layout.source.imports}
    origins: dict[str, list[Origin | None]] = {}
    failures = []
    for name, lines in rendered(layout, line_limit(root)).items():
        path = package / name
        where = str(path.relative_to(root))
        if not path.is_file() or path.read_text(encoding="utf-8") != text_of(lines):
            failures.append(f"{where} is not what the plan renders from the recorded source")
            continue
        for number, (text, origin) in enumerate(lines, start=1):
            if origin is None:
                continue
            if origin.copied and text != layout.source.lines[origin.first - 1]:
                failures.append(f"{where}:{number} is called a copy of source line "
                                f"{origin.first} and is not")
            if not origin.copied and (origin.first, origin.last) not in spans:
                failures.append(f"{where}:{number} is called an import from source lines "
                                f"{origin.first}-{origin.last}, which hold no import")
        composed = [lines[node.lineno - 1][1] is None for node in ast.parse(
            text_of(lines)).body if isinstance(node, (ast.Import, ast.ImportFrom))]
        if True in composed and False in composed[composed.index(True):]:
            failures.append(f"{where}: an import the writer composed sits before a "
                            "source-derived one")
        origins[where] = [origin for _, origin in lines]
    return origins, failures


def import_block(layout: Layout) -> tuple[int, int]:
    """The source's import block, from its first top-level import's line to its last's end
    (an empty span when it has none)."""
    imports = layout.source.imports
    if not imports:
        return 1, 0
    return imports[0].node.lineno, imports[-1].node.end_lineno


def describe(layout: Layout) -> dict[str, int]:
    """How many whole nodes and split-class members the proof held, for its report."""
    whole = sum(1 for u in layout.source.units if u.name not in layout.split)
    members = sum(len(s.members) for s in layout.split.values())
    return {"whole_nodes": whole, "split_members": members}
