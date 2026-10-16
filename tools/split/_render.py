"""Turn a layout into files: each output's text, the package on disk, and the importers'
rewritten lines.

**The writer copies, it never composes.**  A moved block is the source's own lines, sliced by
line number; the only text it generates is what the layout requires and the prover checks
by shape: module docstrings, import statements filtered from the source's own (in the
source's order and blank-line groups), a split class's ``class`` line and one-line
docstring, and its mixin's ``class`` line.  Nothing is written until every output has
rendered under the line budget and every importer's line has been re-read (:func:`write`),
so a refusal leaves the tree as it was; and an apply that fails anywhere, its proof of what
it wrote included, puts it back, or names what it could not (all but a process ended by a
signal Python does not raise as an exception: :func:`write`).

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
import contextlib
import hashlib
import stat
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from tools.split._classes import mixin_name
from tools.split._layout import Layout, Output, Piece
from tools.split._model import HELPERS, INIT
from tools.split._run import RunError, python_files
from tools.split._source import (
    ImportStmt,
    Refusal,
    bound_name,
    decode,
    imported,
    lines_of,
    parse,
    read_ini,
    text_holding,
    within,
)

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
    """``.pylintrc``'s ``max-line-length``, the width a generated line may not exceed;
    refused when it states none, or one that is not a whole number."""
    parser = read_ini(root / ".pylintrc")
    try:
        return int(parser["FORMAT"]["max-line-length"])
    except KeyError as exc:
        raise Refusal(".pylintrc states no [FORMAT] max-line-length") from exc
    except ValueError as exc:
        raise Refusal(f".pylintrc's max-line-length is not a whole number ({exc})") from exc


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


def _importer_rewrite(layout: Layout, path: Path, node: ast.AST, lines: list[str],
                      package: str) -> Rewrite | None:
    """The rewrite one import statement in another module needs, None when it does not
    import the source, or a refusal when the tool cannot point it anywhere single.
    ``package`` is the importing module's package, against which a relative import is
    resolved (:func:`tools.split._source.imported`).  An absolute import from INSIDE the
    package the source becomes is the re-pointed form, which the prover holds to what
    ``_helpers.py`` holds (:func:`tools.split._prove._check_importers`), so it needs none."""
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        return None
    dotted = layout.plan.dotted
    if not any(imported(node, alias, package).names(dotted) for alias in node.names):
        return None
    where = f"{path}:{node.lineno}"
    if isinstance(node, ast.Import):
        raise Refusal(f"{where}: imports the source as a module; rewrite it by hand first")
    if node.level:
        raise Refusal(f"{where}: a relative import of the source")
    if within(node.module, dotted) and node.module != dotted:
        return None
    if node.module != dotted:
        raise Refusal(f"{where}: imports the source module by name from its package")
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
    (:func:`tools.split._run.python_files`), rewritten to ``_helpers.py``.  The source and the
    package it becomes are the split itself, not modules importing it: a part's own
    ``from ._helpers import`` names the package, and is the writer's."""
    source = (root / layout.plan.source).resolve()
    package = (root / layout.plan.package).resolve()
    found = []
    for path in python_files(root):
        if path.resolve() == source or path.resolve().is_relative_to(package):
            continue
        text = text_holding(path, layout.plan.stem)
        if text is None:
            continue
        lines = lines_of(text)
        its_package = ".".join(path.relative_to(root).parent.parts)
        here = [rewrite for rewrite in (_importer_rewrite(layout, path, node, lines, its_package)
                                        for node in ast.walk(parse(text, str(path))))
                if rewrite]
        if here and "\r" in text:
            raise Refusal(f"{path}: a carriage return in its line endings, which the writer, "
                          "reading and writing text, would not keep")
        found += here
    return found


class Held(NamedTuple):
    """A file as it was before :func:`write`: its bytes and its permission bits."""

    data: bytes
    mode: int


def _hold(path: Path) -> Held:
    """``path``'s bytes and permission bits, as :func:`_undo` puts them back."""
    return Held(path.read_bytes(), stat.S_IMODE(path.stat().st_mode))


@contextlib.contextmanager
def write(layout: Layout, root: Path, texts: dict[str, str], rewrites: list[Rewrite],
          beside: dict[Path, str]) -> Iterator[None]:
    """Write one apply as ONE unit: each file ``beside`` the tree (the id maps, text by path),
    then the package, the source's deletion and the importers' rewrites; the body of the
    ``with`` statement (the apply's proof of what was written) runs inside the same unit.

    Every check that writes nothing runs before the first write, so a refusal leaves the tree
    as it was (the branch's neutral review, F8a: the importers' re-check ran after the package
    was written and the source deleted; and delta review 2's P1: an id map whose directory
    did not exist failed after the package was written).  Each importer is read once and
    rewritten whole, so two rewrites in one file are applied together.

    Anything that escapes the unit undoes it before it propagates, the static proof's
    refusal of what was written included (the coordinator's ruling on commit 3b's (A), gaps
    closed, 2026-10-15: the tree never holds an unproved split): the source's, every
    importer's and every existing file beside the tree's bytes and permission bits are held
    from before the first write, so every file it created is removed and every file it
    changed put back, its mode with it.  An :class:`OSError` (a full disk, a permission)
    becomes a :class:`tools.split._run.RunError`, and a :class:`Refusal` or ``RunError`` stays
    one, each saying what was undone or naming what could not be; anything else (Ctrl-C,
    which Python raises as :class:`KeyboardInterrupt`) propagates as it was, after the undo,
    with a note saying the same.  A signal Python does not raise as an exception (by default
    ``SIGTERM`` and ``SIGHUP``, and always ``SIGKILL``) ends the process where it stands, and
    a machine that stops does too: no undo runs, and the tree is left as far as the write
    got.  The unit undone is THE APPLY, not the
    tree (the same ruling): lane A maps test ids by the id maps, so a map left for a split
    that never landed would mislead it.  Each held state is taken before any write, so two
    spellings of one path beside the tree are undone alike."""
    package = root / layout.plan.package
    if package.exists():
        raise Refusal(f"{package} already exists")
    for path in beside:
        if not path.parent.is_dir():
            raise Refusal(f"{path}: no directory {path.parent} to write it in; nothing is "
                          "applied")
    source = root / layout.plan.source
    originals, edited = _rewritten(rewrites)
    originals[source] = _hold(source)
    held = {path: _hold(path) for path in beside if path.exists()}
    originals.update(held)
    created: list[Path] = []
    changed: list[Path] = []
    try:
        _write_beside(beside, held, (created, changed))
        _write_steps(package, texts, source, edited, (created, changed))
        yield
    except OSError as exc:
        raise RunError(f"the apply failed ({exc}); {_undone(created, changed, originals)}"
                       ) from exc
    except (Refusal, RunError) as exc:
        raise type(exc)(f"{exc}; {_undone(created, changed, originals)}") from exc
    except BaseException as exc:
        exc.add_note(f"tools.split: the apply was undone: "
                     f"{_undone(created, changed, originals)}")
        raise


def _rewritten(rewrites: list[Rewrite]) -> tuple[dict[Path, Held], dict[Path, list[str]]]:
    """Each importer as read, and its lines with every rewrite applied; refused if a line to
    rewrite is not what it was when the rewrite was derived."""
    originals: dict[Path, Held] = {}
    edited: dict[Path, list[str]] = {}
    for rewrite in rewrites:
        if rewrite.path not in edited:
            originals[rewrite.path] = _hold(rewrite.path)
            edited[rewrite.path] = decode(originals[rewrite.path].data,
                                          str(rewrite.path)).split("\n")
        lines = edited[rewrite.path]
        if lines[rewrite.line - 1] != rewrite.old:
            raise Refusal(f"{rewrite.path}:{rewrite.line} changed since it was read")
        lines[rewrite.line - 1] = rewrite.new
    return originals, edited


def _write_beside(beside: dict[Path, str], held: dict[Path, Held],
                  progress: tuple[list[Path], list[Path]]) -> None:
    """:func:`write`'s first writes, each file beside the tree; each goes into ``progress``
    BEFORE it is written: changed when ``held`` holds it from before, else created."""
    created, changed = progress
    for path, text in beside.items():
        (changed if path in held else created).append(path)
        path.write_text(text, encoding="utf-8")


def _write_steps(package: Path, texts: dict[str, str], source: Path,
                 edited: dict[Path, list[str]], progress: tuple[list[Path], list[Path]]
                 ) -> None:
    """:func:`write`'s writes, in order: the package and its files, the source's deletion,
    each importer.  Each file goes into ``progress`` (what was created, what was changed)
    BEFORE its step runs, so a step that fails part-way is undone with the rest; the
    package goes in once made, because a ``mkdir`` that fails made nothing."""
    created, changed = progress
    package.mkdir()
    created.append(package)
    for name, text in texts.items():
        created.append(package / name)
        (package / name).write_text(text, encoding="utf-8")
    changed.append(source)
    source.unlink()
    for path, lines in edited.items():
        changed.append(path)
        path.write_text("\n".join(lines), encoding="utf-8")


def _undone(created: list[Path], changed: list[Path], originals: dict[Path, Held]) -> str:
    """Undo a failed :func:`write` (:func:`_undo`) and say what it did."""
    failed = _undo(created, changed, originals)
    return (f"NOT undone: {'; '.join(failed)}" if failed else
            "every file it created is removed and every file it changed put back")


def _undo(created: list[Path], changed: list[Path], originals: dict[Path, Held]
          ) -> list[str]:
    """Undo a failed :func:`write`: remove what it created, newest first (the package's
    files, then the package, then the files beside the tree), which frees their space before
    anything is put back; then write back, from its bytes before the write, each file it
    began to change that no longer holds them (a step that failed before it touched its file
    left nothing to put back), and give each its permission bits from before (a deleted
    source is re-created with the process's default mode).  Each step that fails is named,
    and the rest still run."""
    failed = []
    for path in reversed(created):
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            failed.append(f"{path}: {exc}")
    for path in changed:
        data, mode = originals[path]
        try:
            if not (path.is_file() and path.read_bytes() == data):
                path.write_bytes(data)
            if stat.S_IMODE(path.stat().st_mode) != mode:
                path.chmod(mode)
        except OSError as exc:
            failed.append(f"{path}: {exc}")
    return failed
