"""R0801 (duplicate-code) between parts, sorted into REVEALED and CREATED (the coordinator's
correction to Fork 5 item 4, ruled 2026-10-15).

**The source's count of zero was structurally empty**: pylint's similarity checker compares
different files and never a file with itself, so a split reveals duplication its source
always held.  What the arm must refuse is duplication the split CREATED.  A pair between two
parts is accepted only when BOTH of its ranges are each ONE VERBATIM SOURCE RUN, map back to
two DISTINCT source ranges, and pylint's own :class:`~pylint.checkers.symilar.Symilar`, run
at the checker's own defaults (the repository's ``.pylintrc`` sets no ``[SIMILARITIES]``),
reports those two source ranges as a pair when fed them.  Each side is fed as the source
module with every line outside its range blanked, so line numbers stay those of the
module's own syntax tree (which the checker uses to find imports and signatures) and its
normalization stays its own.

**The unit is a verbatim source run, never one moved block** (the coordinator's ruling on
the split session's fork over test_salary, 2026-10-15): a run may cross from one moved block
into the next where the two were adjacent in the source, as two tests of a split class are.
A range is one when (1) every non-blank line in it is a moved line (a whole node's block or
a split class's test member; never an import, a docstring, a slice's ``class`` line or a
mixin), and, over its KEPT SPAN, (2) every non-blank line sits at ONE distance from its
source line, so blocks that were not adjacent in the source, or whose gap the renderer
changed, cannot pass, and (3) the text is line for line the source's at that distance,
blank lines included.

**The kept span, not pylint's reported range** (the coordinator's ruling on the split
session's fork over test_loan's re-proof, 2026-10-15): pylint reports a range that runs
past the last line its similarity checker compares, through blanks, comments, signatures and
docstrings up to the next line it compares, so it can take in the leading comment of a block
that was not adjacent in the source.  (2) and (3) hold from the first to the last line the
checker's own normalization (:func:`~pylint.checkers.symilar.stripped_lines`) keeps: the
lines it compares.  (1) still covers the whole reported range.
"""
from __future__ import annotations

import configparser
import contextlib
import io
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath

import astroid
from pylint.checkers.symilar import SimilaritiesChecker, Symilar, stripped_lines

from tools.split._classes import mixin_name
from tools.split._dynamic import DUPLICATE_CODE
from tools.split._layout import Layout
from tools.split._model import HELPERS
from tools.split._source import Block, CollectRules, Refusal, blocks, read_module

#: One side of a pylint duplicate-code report: ``==<dotted module>:[<start>:<end>]``,
#: 0-based and end-exclusive.
_SIDE = re.compile(r"^==(?P<module>[\w.]+):\[(?P<start>\d+):(?P<end>\d+)\]$", re.M)


@dataclass(frozen=True)
class Side:
    """One range of a reported pair: a file, and its 0-based, end-exclusive line range."""

    path: str
    start: int
    end: int

    def __str__(self) -> str:
        """The side as pylint writes one: ``<path>:[<start>:<end>]``."""
        return f"{self.path}:[{self.start}:{self.end}]"


def pairs(messages: list[dict]) -> list[tuple[Side, Side]]:
    """Every pair of ranges pylint's duplicate-code messages report (a message naming N files
    is N*(N-1)/2 pairs)."""
    found = []
    for message in messages:
        if message["symbol"] != DUPLICATE_CODE:
            continue
        sides = [Side(str(PurePosixPath(*m["module"].split("."))) + ".py", int(m["start"]),
                      int(m["end"])) for m in _SIDE.finditer(message["message"])]
        found.extend(combinations(sides, 2))
    return found


@dataclass(frozen=True)
class _Moved:
    """One output file's lines, the 0-based lines pylint's own normalization keeps in it,
    and ``(output block, source block)`` for every verbatim moved block in it: a whole node,
    or a split class's test member (a mixin is not one)."""

    lines: tuple[str, ...]
    kept: frozenset[int]
    blocks: tuple[tuple[Block, Block], ...]

    def block_of(self, line: int) -> tuple[Block, Block] | None:
        """The moved block holding 1-based ``line``, if any."""
        return next((pair for pair in self.blocks if pair[0].first <= line <= pair[0].end),
                    None)


def _moved_blocks(layout: Layout, root: Path, rules: CollectRules,
                  flags: tuple[bool, ...]) -> dict[str, _Moved]:
    """Each output file's :class:`_Moved`, by repository-relative path."""
    mixins = {mixin_name(n) for n in layout.split}
    found: dict[str, _Moved] = {}
    for module in layout.outputs:
        path = layout.plan.package / f"{module}.py"
        out = read_module(root / path, rules, relative=HELPERS)
        moved: list[tuple[Block, Block]] = []
        for unit in out.units:
            if unit.name in layout.split and module != HELPERS:
                node = unit.block.node
                members = {m.name: m for m in layout.split[unit.name].members}
                moved.extend((m, members[m.name]) for m in blocks(
                    node.body[1:], node.body[0].end_lineno, out.lines, module))
            elif unit.name not in mixins and layout.source.unit(unit.name) is not None:
                moved.append((unit.block, layout.source.unit(unit.name).block))
        text = (root / path).read_text(encoding="utf-8")
        kept = frozenset(spec.line_number for spec in stripped_lines(
            text.splitlines(keepends=True), *flags, tree=astroid.parse(text)))
        found[str(path)] = _Moved(lines=out.lines, kept=kept, blocks=tuple(moved))
    return found


def _to_source(side: Side, moved: dict[str, _Moved],
               source: tuple[str, ...]) -> tuple[int, int] | str:
    """The 0-based, end-exclusive source range a side is a verbatim copy of (the span of the
    lines pylint's normalization keeps in it), or why it is not one verbatim source run."""
    here = moved.get(side.path)
    if here is None:
        return "a range lies in no output file"
    body = [i for i in range(side.start, side.end) if here.lines[i].strip()]
    if not body or any(here.block_of(i + 1) is None for i in body):
        return "a range touches a generated line"
    kept = [i for i in range(side.start, side.end) if i in here.kept]
    if not kept:
        return "a range holds no line pylint's normalization keeps"
    first, end = kept[0], kept[-1] + 1
    offsets = {original.first - out.first for out, original in (
        here.block_of(i + 1) for i in range(first, end) if here.lines[i].strip())}
    if len(offsets) != 1:
        return ("a range spans moved blocks that are not one source run (not adjacent in "
                "the source, or a gap the renderer changed)")
    offset = offsets.pop()
    if here.lines[first:end] != source[first + offset:end + offset]:
        return "a range's text differs from the source's at its distance"
    return first + offset, end + offset


def checker_defaults(root: Path) -> dict[str, object]:
    """The similarity checker's own option defaults, refused when ``.pylintrc`` overrides
    them (then the repository's config, not the defaults, would be the measure)."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(root / ".pylintrc", encoding="utf-8")
    if parser.has_section("SIMILARITIES"):
        raise Refusal(".pylintrc sets [SIMILARITIES]; read it before trusting the defaults")
    return {name: spec["default"] for name, spec in SimilaritiesChecker.options}


def _couples(report: str) -> list[dict[str, tuple[int, int]]]:
    """Each couple a Symilar report lists, as its sides' spans by stream name."""
    found = []
    for block in re.split(r"^\d+ similar lines in \d+ files$", report, flags=re.M)[1:]:
        found.append({m["module"]: (int(m["start"]), int(m["end"]))
                      for m in _SIDE.finditer(block)})
    return found


def _reported(source_text: str, first: tuple[int, int], second: tuple[int, int],
              options: dict[str, object]) -> bool:
    """Whether pylint's Symilar, fed the two source ranges, reports ONE couple between them
    that spans every line its own normalization keeps from each range."""
    lines = source_text.splitlines(keepends=True)
    tree = astroid.parse(source_text)
    flags = (options["ignore-comments"], options["ignore-docstrings"],
             options["ignore-imports"], options["ignore-signatures"])
    symilar = Symilar(options["min-similarity-lines"], *flags)
    kept_lines: dict[str, list[int]] = {}
    for name, (start, end) in (("first", first), ("second", second)):
        stream = [line if start <= i < end else "\n" for i, line in enumerate(lines)]
        kept_lines[name] = [spec.line_number for spec in stripped_lines(
            stream, *flags, tree=tree)]
        symilar.append_stream(name, io.StringIO("".join(stream)), tree=tree)
    report = io.StringIO()
    with contextlib.redirect_stdout(report):
        symilar.run()
    return any(
        all(name in couple and kept and couple[name][0] <= kept[0] and kept[-1] < couple[name][1]
            for name, kept in kept_lines.items())
        for couple in _couples(report.getvalue()))


@dataclass(frozen=True)
class Sorted:
    """The pairs, sorted: each revealed one with its two source ranges, each created one
    with why."""

    revealed: list[tuple[Side, Side, tuple[int, int], tuple[int, int]]]
    created: list[tuple[Side, Side, str]]


def sort_pairs(layout: Layout, root: Path, rules: CollectRules, messages: list[dict],
               source_text: str) -> Sorted:
    """Sort every duplicate-code pair between the parts into revealed and created."""
    options = checker_defaults(root)
    flags = (options["ignore-comments"], options["ignore-docstrings"],
             options["ignore-imports"], options["ignore-signatures"])
    moved = _moved_blocks(layout, root, rules, flags)
    revealed, created = [], []
    for first, second in pairs(messages):
        mapped = (_to_source(first, moved, layout.source.lines),
                  _to_source(second, moved, layout.source.lines))
        refused = [why for why in mapped if isinstance(why, str)]
        if refused:
            created.append((first, second, refused[0]))
        elif not (mapped[0][1] <= mapped[1][0] or mapped[1][1] <= mapped[0][0]):
            created.append((first, second, "the two ranges map to one source range"))
        elif not _reported(source_text, mapped[0], mapped[1], options):
            created.append((first, second, "pylint does not report the source ranges"))
        else:
            revealed.append((first, second, mapped[0], mapped[1]))
    return Sorted(revealed=revealed, created=created)
