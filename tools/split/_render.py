"""Turn a layout into files: each output's text, the package on disk, and the importers'
rewritten lines.

**The writer copies, it never composes.**  A moved block is the source's own lines, sliced by
line number; the only text it generates is what the layout requires and the prover checks
by shape: module docstrings, import statements filtered from the source's own (in the
source's order and blank-line groups), a split class's ``class`` line and one-line
docstring, and its mixin's ``class`` line.  Nothing is written until every output has
rendered under the line budget, so a refusal leaves the tree as it was.
"""
from __future__ import annotations

import ast
import configparser
import textwrap
from dataclasses import dataclass
from pathlib import Path

from tools.split._classes import mixin_name
from tools.split._layout import Layout, Output, Piece
from tools.split._model import HELPERS, INIT
from tools.split._source import Refusal, bound_name

#: R-BAL301 (Josh, 2026-10-15): "every Python file under 1,000 lines".  A part may hold at
#: most ``LINE_BUDGET - 1`` lines.
LINE_BUDGET = 1000

#: Where a module that imports the split source may live.
IMPORTER_ROOTS = ("tests", "tools", "scripts", "app")


def line_limit(root: Path) -> int:
    """``.pylintrc``'s ``max-line-length``, the width a generated line may not exceed."""
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(root / ".pylintrc", encoding="utf-8"):
        raise Refusal(f"no {root / '.pylintrc'} to read max-line-length from")
    try:
        return int(parser["FORMAT"]["max-line-length"])
    except KeyError as exc:
        raise Refusal(".pylintrc states no [FORMAT] max-line-length") from exc


def _checked(lines: list[str], limit: int, what: str) -> list[str]:
    """``lines``, refused if any generated line is wider than ``limit``."""
    wide = [line for line in lines if len(line) > limit]
    if wide:
        raise Refusal(f"{what}: a generated line exceeds {limit} columns: {wide[0]!r}")
    return lines


def docstring_lines(text: str, limit: int, what: str) -> list[str]:
    """A module docstring as written: one line when it fits on one, a one-paragraph text
    that does not fit wrapped to the limit, and a text with its own line breaks kept as the
    plan wrote it (refused if any of its lines is too wide)."""
    body = text.strip("\n").split("\n")
    if len(body) == 1 and len(f'"""{body[0]}"""') > limit:
        body = textwrap.wrap(body[0], width=limit - len('"""'), break_long_words=False,
                             break_on_hyphens=False)
    if len(body) == 1:
        return _checked([f'"""{body[0]}"""'], limit, what)
    return _checked([f'"""{body[0]}', *body[1:], '"""'], limit, what)


def _from_lines(module: str, names: list[str], limit: int) -> list[str]:
    """``from <module> import <names>`` on one line when it fits, else one name a line."""
    head = f"from {module} import "
    one = head + ", ".join(names)
    if len(one) <= limit:
        return [one]
    return [head + "(", *(f"    {name}," for name in names), ")"]


def import_lines(layout: Layout, output: Output, limit: int) -> list[str]:
    """The output's imports: each source statement filtered to the aliases it reads (a
    ``from __future__`` import always), in source order and blank-line groups, then the
    names it reads from ``_helpers.py``."""
    lines: list[str] = []
    group = None
    for stmt in layout.source.imports:
        kept = [a for a in stmt.node.names
                if stmt.is_future or bound_name(a, stmt.node) in output.names]
        if not kept:
            continue
        if group is not None and stmt.group != group:
            lines.append("")
        group = stmt.group
        if isinstance(stmt.node, ast.Import):
            lines.append(ast.unparse(ast.Import(names=kept)))
        else:
            lines.extend(_from_lines(stmt.node.module, [ast.unparse(a) for a in kept], limit))
    if output.helpers:
        lines.extend(([""] if lines else []) + _from_lines(f".{HELPERS}", output.helpers, limit))
    return _checked(lines, limit, output.module)


def piece_lines(layout: Layout, piece: Piece) -> list[str]:
    """One piece's lines: a whole unit's block verbatim, or a slice / mixin built from its
    class's member blocks verbatim."""
    lines = layout.source.lines
    shape = piece.shape
    if shape is None:
        return list(layout.source.unit(piece.binds).block.text(lines))
    by_name = {m.name: m for m in shape.members}
    if piece.slice is None:
        start, end = shape.docstring
        out = [f"class {piece.binds}:", *lines[start - 1:end]]
        for member in shape.non_tests():
            out += ["", *member.text(lines)]
        return out
    block = shape.unit.block
    out = list(lines[block.first - 1:block.start - 1]) if piece.first_slice else []
    out += [f"class {shape.name}({mixin_name(shape.name)}):", f'    """{piece.slice.docstring}"""']
    for name in piece.slice.tests:
        out += ["", *by_name[name].text(lines)]
    return out


def render_output(layout: Layout, output: Output, limit: int) -> str:
    """The full text of one output module."""
    lines = docstring_lines(output.docstring, limit, output.module)
    imports = import_lines(layout, output, limit)
    if imports:
        lines += ["", *imports]
    for piece in output.pieces:
        lines += ["", "", *piece_lines(layout, piece)]
    return "\n".join(lines) + "\n"


def render_init(layout: Layout) -> str:
    """The package's ``__init__.py``: the source's module docstring, verbatim."""
    end = layout.source.docstring_end
    if not end:
        raise Refusal(f"{layout.plan.source} has no module docstring for its package")
    return "\n".join(layout.source.lines[:end]) + "\n"


def render_all(layout: Layout, limit: int) -> dict[str, str]:
    """Every file the package holds, by file name, refused if any is over the budget."""
    texts = {f"{INIT}.py": render_init(layout)}
    for output in layout.outputs.values():
        texts[f"{output.module}.py"] = render_output(layout, output, limit)
    over = {name: text.count("\n") for name, text in texts.items()
            if text.count("\n") >= LINE_BUDGET}
    if over:
        raise Refusal(f"over the {LINE_BUDGET}-line budget: {over}")
    return texts


@dataclass(frozen=True)
class Rewrite:
    """One importer's import line, before and after it is pointed at ``_helpers.py``."""

    path: Path
    line: int
    old: str
    new: str


def _importer_rewrite(layout: Layout, path: Path, node: ast.AST, lines: list[str]
                      ) -> Rewrite | None:
    """The rewrite one import statement in another module needs, None when it does not
    import the source, or a refusal when the tool cannot point it anywhere single."""
    dotted = layout.plan.dotted
    parent, _, stem = dotted.rpartition(".")
    where = f"{path}:{getattr(node, 'lineno', '?')}"
    if isinstance(node, ast.Import) and any(
            a.name == dotted or a.name.startswith(dotted + ".") for a in node.names):
        raise Refusal(f"{where}: imports the source as a module; rewrite it by hand first")
    if not isinstance(node, ast.ImportFrom):
        return None
    if node.level == 0 and node.module == parent and any(a.name == stem for a in node.names):
        raise Refusal(f"{where}: imports the source module by name from its package")
    if node.level and (node.module == stem or (node.module is None and any(
            a.name == stem for a in node.names))):
        raise Refusal(f"{where}: a relative import of the source")
    if node.level or node.module != dotted:
        return None
    homes = {layout.home.get(a.name) for a in node.names}
    if homes != {HELPERS}:
        raise Refusal(f"{where}: imports {[a.name for a in node.names]}, homed at {homes}; "
                      "only helpers may be imported across packages")
    old = lines[node.lineno - 1]
    needle = f"from {dotted} import"
    if old.count(needle) != 1:
        raise Refusal(f"{where}: the import does not read {needle!r} on its first line")
    return Rewrite(path, node.lineno, old, old.replace(needle, f"from {dotted}.{HELPERS} import"))


def importer_rewrites(layout: Layout, root: Path) -> list[Rewrite]:
    """Every import of the source in another module, rewritten to ``_helpers.py``."""
    source = (root / layout.plan.source).resolve()
    found = []
    for top in IMPORTER_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            if "__pycache__" in path.parts or path.resolve() == source:
                continue
            text = path.read_text(encoding="utf-8")
            if layout.plan.stem not in text:
                continue
            lines = text.splitlines()
            for node in ast.walk(ast.parse(text, filename=str(path))):
                rewrite = _importer_rewrite(layout, path, node, lines)
                if rewrite:
                    found.append(rewrite)
    return found


def write(layout: Layout, root: Path, texts: dict[str, str], rewrites: list[Rewrite]) -> None:
    """Create the package, delete the source, and apply the importers' rewrites."""
    package = root / layout.plan.package
    if package.exists():
        raise Refusal(f"{package} already exists")
    package.mkdir()
    for name, text in texts.items():
        (package / name).write_text(text, encoding="utf-8")
    (root / layout.plan.source).unlink()
    for rewrite in rewrites:
        lines = rewrite.path.read_text(encoding="utf-8").split("\n")
        if lines[rewrite.line - 1] != rewrite.old:
            raise Refusal(f"{rewrite.path}:{rewrite.line} changed since it was read")
        lines[rewrite.line - 1] = rewrite.new
        rewrite.path.write_text("\n".join(lines), encoding="utf-8")
