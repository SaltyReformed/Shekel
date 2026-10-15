"""Turn a layout into files: each output's text, the package on disk, and the importers'
rewritten lines.

**The writer copies, it never composes.**  A moved block is the source's own lines, sliced by
line number; the only text it generates is what the layout requires and the prover checks
by shape: module docstrings, import statements filtered from the source's own (in the
source's order and blank-line groups), a split class's ``class`` line and one-line
docstring, and its mixin's ``class`` line.  Nothing is written until every output has
rendered under the line budget and every importer's line has been re-read (:func:`write`),
so a refusal leaves the tree as it was.

Every line it renders carries its mark: an :class:`Origin` (the source line it copies, or
the source statement an import was filtered from), :data:`PROVENANCE` for a composed line
naming the source the package came from, or nothing for any other line it composed.  The
prover judges each pylint message by that origin (:func:`tools.split._dynamic.compare_pylint`),
after checking every claim against the files on disk and the source
(:func:`._prove.line_origins`); the census drops exactly the provenance lines
(:func:`tools.split._dynamic.classify`).
"""
from __future__ import annotations

import ast
import configparser
import hashlib
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from tools.split._classes import mixin_name
from tools.split._layout import Layout, Output, Piece
from tools.split._model import HELPERS, INIT
from tools.split._run import python_files
from tools.split._source import ImportStmt, Refusal, bound_name

#: R-BAL301 (Josh, 2026-10-15): "every Python file under 1,000 lines".  A part may hold at
#: most ``LINE_BUDGET - 1`` lines.
LINE_BUDGET = 1000


class Origin(NamedTuple):
    """Where one written line came from: the 1-based source lines a pylint message on it is
    matched against.  ``copied``: the line is a verbatim copy of source line ``first`` (and
    ``last`` is ``first``); otherwise it is part of an import filtered from the source
    statement on lines ``first`` to ``last``."""

    first: int
    last: int
    copied: bool


@dataclass(frozen=True)
class Provenance:
    """The mark on a line the writer composed to say where the package came from: a part's
    closing "Split from" paragraph, and every line of ``conftest.py``'s and ``_helpers.py``'s
    docstrings (the coordinator's ruling on the follow-up leaf's fork D, 2026-10-15).  Like
    any composed line it has no source line, so no pylint message may be matched to it;
    unlike one, the census drops it, because it names the old module by design.  The plan's
    own text is never marked, so a line of it naming the old module is listed."""


#: The one :class:`Provenance` mark.
PROVENANCE = Provenance()

#: What a written line came from: the source (an :class:`Origin`), the package's provenance
#: (:data:`PROVENANCE`), or nothing, for any other line the writer composed.
Mark = Origin | Provenance | None

#: One written line and its mark.
Line = tuple[str, Mark]


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


def _wrapped(paragraph: str, width: int) -> list[str]:
    """One paragraph wrapped to ``width``, never breaking a word or at a hyphen."""
    return textwrap.wrap(paragraph, width=width, break_long_words=False, break_on_hyphens=False)


def docstring_lines(output: Output, limit: int) -> list[Line]:
    """An output's module docstring as written, each line with its mark.

    A part's is the plan's text, kept as the plan wrote it, then a blank line and the tool's
    provenance (:func:`tools.split._layout.split_from`) wrapped to the limit.
    ``conftest.py``'s and ``_helpers.py``'s is the provenance alone: one line when it fits on
    one, else wrapped to the limit with the opening quotes counted.  Each provenance line is
    marked :data:`PROVENANCE`, every other line nothing.  Refused if any line is too wide."""
    if output.docstring:
        body: list[Line] = [(line, None) for line in output.docstring.strip("\n").split("\n")]
        body += [("", None), *((line, PROVENANCE) for line in _wrapped(output.provenance, limit))]
    elif len(f'"""{output.provenance}"""') <= limit:
        body = [(output.provenance, PROVENANCE)]
    else:
        body = [(line, PROVENANCE)
                for line in _wrapped(output.provenance, limit - len('"""'))]
    first, mark = body[0]
    if len(body) == 1:
        lines = [(f'"""{first}"""', mark)]
    else:
        lines = [(f'"""{first}', mark), *body[1:], ('"""', None)]
    _checked([text for text, _ in lines], limit, output.module)
    return lines


def _from_lines(module: str, names: list[str], limit: int) -> list[str]:
    """``from <module> import <names>`` on one line when it fits, else one name a line."""
    head = f"from {module} import "
    one = head + ", ".join(names)
    if len(one) <= limit:
        return [one]
    return [head + "(", *(f"    {name}," for name in names), ")"]


def import_lines(layout: Layout, output: Output, limit: int
                 ) -> list[tuple[ImportStmt | None, list[str]]]:
    """The output's imports, each with the source statement it was filtered to the aliases it
    reads from (a ``from __future__`` import always), in source order and blank-line groups,
    then the names it reads from ``_helpers.py``.  A blank separator and the ``_helpers``
    import are the writer's own, so they carry no statement."""
    found: list[tuple[ImportStmt | None, list[str]]] = []
    group = None
    for stmt in layout.source.imports:
        kept = [a for a in stmt.node.names
                if stmt.is_future or bound_name(a, stmt.node) in output.names]
        if not kept:
            continue
        if group is not None and stmt.group != group:
            found.append((None, [""]))
        group = stmt.group
        if isinstance(stmt.node, ast.Import):
            lines = [ast.unparse(ast.Import(names=kept))]
        else:
            lines = _from_lines(stmt.node.module, [ast.unparse(a) for a in kept], limit)
        found.append((stmt, _checked(lines, limit, output.module)))
    if output.helpers:
        found.extend(([(None, [""])] if found else []) + [(None, _checked(
            _from_lines(f".{HELPERS}", output.helpers, limit), limit, output.module))])
    return found


def _generated(lines: list[str]) -> list[Line]:
    """Lines the writer composed, which no message may be matched to."""
    return [(line, None) for line in lines]


def _copied(lines: tuple[str, ...], first: int, last: int) -> list[Line]:
    """Source lines ``first`` to ``last`` (1-based, inclusive), each from itself."""
    return [(lines[n - 1], Origin(n, n, copied=True)) for n in range(first, last + 1)]


def _piece_lines(layout: Layout, piece: Piece) -> list[Line]:
    """One piece's lines: a whole unit's block verbatim, or a slice / mixin built from its
    class's member blocks verbatim."""
    lines = layout.source.lines
    shape = piece.shape
    if shape is None:
        block = layout.source.unit(piece.binds).block
        return _copied(lines, block.first, block.end)
    by_name = {m.name: m for m in shape.members}
    if piece.slice is None:
        start, end = shape.docstring
        out = [(f"class {piece.binds}:", None), *_copied(lines, start, end)]
        for member in shape.non_tests():
            out += [("", None), *_copied(lines, member.first, member.end)]
        return out
    block = shape.unit.block
    out = _copied(lines, block.first, block.start - 1) if piece.first_slice else []
    out += _generated([f"class {shape.name}({mixin_name(shape.name)}):",
                       f'    """{piece.slice.docstring}"""'])
    for name in piece.slice.tests:
        out += [("", None), *_copied(lines, by_name[name].first, by_name[name].end)]
    return out


def output_lines(layout: Layout, output: Output, limit: int) -> list[Line]:
    """One output module's lines, each with its origin."""
    found = docstring_lines(output, limit)
    imports: list[Line] = []
    for stmt, lines in import_lines(layout, output, limit):
        origin = None if stmt is None else Origin(stmt.node.lineno, stmt.node.end_lineno,
                                                  copied=False)
        imports += [(line, origin) for line in lines]
    if imports:
        found += [("", None), *imports]
    for piece in output.pieces:
        found += [("", None), ("", None), *_piece_lines(layout, piece)]
    return found


def init_lines(layout: Layout) -> list[Line]:
    """The package's ``__init__.py``: the source's module docstring, verbatim."""
    end = layout.source.docstring_end
    if not end:
        raise Refusal(f"{layout.plan.source} has no module docstring for its package")
    return _copied(layout.source.lines, 1, end)


def rendered(layout: Layout, limit: int) -> dict[str, list[Line]]:
    """Every file the package holds, by file name, as its lines and their origins."""
    found = {f"{INIT}.py": init_lines(layout)}
    for output in layout.outputs.values():
        found[f"{output.module}.py"] = output_lines(layout, output, limit)
    return found


def text_of(lines: list[Line]) -> str:
    """A rendered file's text."""
    return "\n".join(line for line, _ in lines) + "\n"


def render_all(layout: Layout, limit: int) -> dict[str, str]:
    """Every file the package holds, by file name, refused if any is over the budget."""
    texts = {name: text_of(lines) for name, lines in rendered(layout, limit).items()}
    over = {name: text.count("\n") for name, text in texts.items()
            if text.count("\n") >= LINE_BUDGET}
    if over:
        raise Refusal(f"over the {LINE_BUDGET}-line budget: {over}")
    return texts


def fingerprint(text: str) -> str:
    """The sha256 an importer's text is recorded and checked by: ``record`` takes it, ``apply``
    refuses a changed one, and ``prove`` re-derives it from the re-pointed file
    (:func:`tools.split._prove.importer_lines`).  One producer, over the decoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Rewrite:
    """One importer's import line, before and after it is pointed at ``_helpers.py``:
    ``line`` is the line rewritten, the statement's first, and ``last`` the statement's last,
    so ``line`` to ``last`` is the statement whose meaning the re-point changed."""

    path: Path
    line: int
    last: int
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
    return Rewrite(path, node.lineno, node.end_lineno, old,
                   old.replace(needle, f"from {dotted}.{HELPERS} import"))


def importer_rewrites(layout: Layout, root: Path) -> list[Rewrite]:
    """Every import of the source in another module of the working tree
    (:func:`tools.split._run.python_files`), rewritten to ``_helpers.py``."""
    source = (root / layout.plan.source).resolve()
    found = []
    for path in python_files(root):
        if path.resolve() == source:
            continue
        text = path.read_bytes().decode("utf-8")
        if layout.plan.stem not in text:
            continue
        lines = text.splitlines()
        here = [rewrite for rewrite in (_importer_rewrite(layout, path, node, lines)
                                        for node in ast.walk(ast.parse(text, str(path))))
                if rewrite]
        if here and "\r" in text:
            raise Refusal(f"{path}: a carriage return in its line endings, which the writer, "
                          "reading and writing text, would not keep")
        found += here
    return found


def write(layout: Layout, root: Path, texts: dict[str, str], rewrites: list[Rewrite]) -> None:
    """Create the package, delete the source, and apply the importers' rewrites.

    Every check runs before the first write, so a refusal leaves the tree as it was (the
    branch's neutral review, F8a: the importers' re-check ran after the package was written
    and the source deleted).  Each importer is read once and rewritten whole, so two
    rewrites in one file are applied together."""
    package = root / layout.plan.package
    if package.exists():
        raise Refusal(f"{package} already exists")
    edited: dict[Path, list[str]] = {}
    for rewrite in rewrites:
        if rewrite.path not in edited:
            edited[rewrite.path] = rewrite.path.read_text(encoding="utf-8").split("\n")
        lines = edited[rewrite.path]
        if lines[rewrite.line - 1] != rewrite.old:
            raise Refusal(f"{rewrite.path}:{rewrite.line} changed since it was read")
        lines[rewrite.line - 1] = rewrite.new
    package.mkdir()
    for name, text in texts.items():
        (package / name).write_text(text, encoding="utf-8")
    (root / layout.plan.source).unlink()
    for path, lines in edited.items():
        path.write_text("\n".join(lines), encoding="utf-8")
