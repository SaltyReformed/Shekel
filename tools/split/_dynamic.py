"""The RUN half of the proof, as pure comparisons of what the runs observed: the old -> new
node-id map, the collection, every test's outcome, pylint's messages and the census of the
old module's name.  The runs themselves are :mod:`tools.split._run`'s; nothing here starts a
process, so every arm can be shown failing on a hand-built observation.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pylint.checkers.imports import MSGS as IMPORT_MESSAGES

from tools.split._layout import Layout
from tools.split._render import Origin
from tools.split._source import Refusal

#: pylint's symbol for the cross-file duplicate check, judged pair by pair in
#: :mod:`tools.split._similar` (the coordinator's correction to Fork 5 item 4).
DUPLICATE_CODE = "duplicate-code"


def id_map(layout: Layout, before: list[str]) -> dict[str, str]:
    """Each collected node id of the source, to the id it has after the split: only its
    file changes.  Refused unless the map is total and one-to-one."""
    source = str(layout.plan.source)
    mapped: dict[str, str] = {}
    for nodeid in before:
        path, _, rest = nodeid.partition("::")
        if path != source or not rest:
            raise Refusal(f"collected id {nodeid!r} is not a test of {source}")
        parts = rest.split("::")
        top = parts[0].split("[")[0]
        method = parts[1].split("[")[0] if len(parts) > 1 else None
        mapped[nodeid] = f"{layout.plan.package}/{layout.module_of(top, method)}.py::{rest}"
    if len(mapped) != len(before) or len(set(mapped.values())) != len(mapped):
        raise Refusal("the node-id map is not one-to-one")
    return mapped


def compare_collection(mapping: dict[str, str], after: list[str]) -> list[str]:
    """Failures where the split's collection is not the source's under ``mapping``: an id
    missing, an id the map does not produce, or an id collected twice."""
    failures = []
    twice = sorted(i for i, n in Counter(after).items() if n > 1)
    if twice:
        failures.append(f"collected twice: {twice[:5]}")
    expected, got = set(mapping.values()), set(after)
    for nodeid in sorted(expected - got)[:10]:
        failures.append(f"not collected after the split: {nodeid}")
    for nodeid in sorted(got - expected)[:10]:
        failures.append(f"collected after the split, mapped from nothing: {nodeid}")
    return failures


def compare_outcomes(mapping: dict[str, str], before: dict[str, str], after: dict[str, str],
                     scope: set[str] | None = None) -> list[str]:
    """Failures where a test ended differently after the split: each old id in ``scope``
    (every one when None) against its new id, plus any new id the map does not produce."""
    failures = []
    olds = sorted(mapping if scope is None else scope)
    for old in olds:
        new = mapping[old]
        if before.get(old) != after.get(new):
            failures.append(f"{old}: {before.get(old)} before, {after.get(new)} after as {new}")
    produced = {mapping[o] for o in olds}
    for new in sorted(set(after) - produced)[:10]:
        failures.append(f"ran after the split, mapped from nothing: {new}")
    return failures


def outcome_counts(outcomes: dict[str, str]) -> dict[str, int]:
    """How many tests ended each way."""
    return dict(sorted(Counter(outcomes.values()).items()))


def pylint_counts(messages: list[dict]) -> Counter[str]:
    """pylint's JSON messages, counted by symbol."""
    return Counter(m["symbol"] for m in messages)


#: pylint's import-SEQUENCE checks, each with its own message template: whether a statement
#: is flagged depends on which other statements the module holds, so filtering a source's
#: imports can move the flag onto another statement of the same defect.
_SEQUENCE = {symbol: template for template, symbol, *_ in IMPORT_MESSAGES.values()
             if symbol in ("wrong-import-order", "ungrouped-imports")}


def defect(message: dict) -> str | None:
    """For an import-sequence message, the defect it names: its FIRST argument (the import
    placed out of order, or the package not grouped), read back through pylint's own message
    template; None for any other message."""
    template = _SEQUENCE.get(message["symbol"])
    if template is None:
        return None
    pattern = "(.*?)".join(re.escape(part) for part in template.split("%s"))
    found = re.fullmatch(pattern, message["message"], flags=re.S)
    if found is None:
        raise Refusal(f"pylint's {message['symbol']} message {message['message']!r} does not "
                      f"read through its own template {template!r}")
    return found.group(1)


def _answer(left: Counter, message: dict, origin: Origin, block: tuple[int, int]
            ) -> tuple[str, int, str | None] | None:
    """The source message left to answer ``message``: one of its symbol (and, for an
    import-sequence check, naming the same defect) on its origin's lines; failing that, for
    an import-sequence check, one anywhere in the source's import ``block``.  (pylint runs
    those checks over module-level imports only, so their origin is always an import.)"""
    symbol, named = message["symbol"], defect(message)
    spans = [(origin.first, origin.last)]
    if named is not None:
        spans.append(block)
    for first, last in spans:
        for line in range(first, last + 1):
            if left[(symbol, line, named)] > 0:
                return symbol, line, named
    return None


def compare_pylint(before: list[dict], after: list[dict],
                   origins: dict[str, list[Origin | None]], block: tuple[int, int]
                   ) -> list[str]:
    """Failures where the parts hold a pylint message the source did not, each message judged
    by its ORIGIN (``origins``: each written file's line origins, by the path pylint names).

    A message on a line moved verbatim is matched to a message of the same symbol on the
    source line it came from; one on an import line, to a message of the same symbol on the
    source statement that import was filtered from, so a statement two parts both read
    carries its message into both.  Within one file each source message answers at most one.
    A message on a line the writer composed (a docstring, a ``class`` line, a blank, the
    ``_helpers`` import) has no origin and is new.  Messages may disappear.  duplicate-code
    is not judged here: the source's count of it is structurally zero (pylint never compares
    a file with itself), so each pair between parts is judged revealed or created by
    :mod:`tools.split._similar` instead.  (The coordinator's ruling on the split session's
    fork over test_savings_dashboard_service, 2026-10-15: a count per kind was blind to a new
    message hidden by a vanished one, and refused an import two parts both read.)

    **An import-sequence check on a source-derived import** (wrong-import-order,
    ungrouped-imports) may also be answered anywhere in the source's import ``block``, by a
    message naming the SAME defect (the coordinator's ruling on the fork over test_salary's
    re-proof, 2026-10-15).  The static proof holds each part's source-derived imports to a
    subsequence of the source's, in source order, and :func:`tools.split._prove.line_origins`
    holds every composed import after them.  The DEFECT is monotone under deletion: a
    package's imports contiguous in the whole sequence are contiguous in any subsequence, and
    so is every category order, so a defect in a part was a defect of the source whose flag
    filtering moved, as when test_salary's ``import pytest`` split its app imports and the
    statement pylint flagged was filtered out of a part.  Pylint's FLAG is not monotone: it
    tracks a package's ``import x`` and ``from x import y`` apart, so ``from os import path``
    / ``from json import dumps`` / ``import os`` / ``from os import sep`` carries no
    ungrouped-imports while the same block without ``import os`` does (measured on pylint
    4.1.2 by the branch's neutral review, 2026-10-15).  What refuses that case is the second
    condition, not the deletion: the source's block must hold a message of the same symbol
    naming the same package or module, so a defect pylint never flagged in the source is
    never answered from it."""
    held = Counter((m["symbol"], m["line"], defect(m)) for m in before)
    failures = []
    for path in sorted({m["path"] for m in after}):
        left = Counter(held)
        lines = origins.get(path, [])
        for message in sorted((m for m in after if m["path"] == path
                               and m["symbol"] != DUPLICATE_CODE),
                              key=lambda m: (m["line"], m["symbol"])):
            symbol, line = message["symbol"], message["line"]
            origin = lines[line - 1] if 0 < line <= len(lines) else None
            if origin is None:
                failures.append(f"{path}:{line}: {symbol} on a line the writer composed")
                continue
            match = _answer(left, message, origin, block)
            if match is None:
                failures.append(f"{path}:{line}: {_unheld(message, origin, block)}")
            else:
                left[match] -= 1
    return failures


def _unheld(message: dict, origin: Origin, block: tuple[int, int]) -> str:
    """Why no source message answers ``message``: what it names, and where was looked."""
    named = defect(message)
    lines = f"source line(s) {origin.first}-{origin.last}"
    if named is None:
        return f"{message['symbol']}, which {lines} do not hold"
    return (f"{message['symbol']} naming {named!r}, which neither {lines} nor the source's "
            f"import block ({block[0]}-{block[1]}) holds")


def new_kinds(before: list[dict], after: list[dict]) -> dict[str, list[str]]:
    """For each file, the pylint symbols it holds after and did not before (the importers'
    report: a kind new to an edited importer is named, not refused)."""
    def by_file(messages: list[dict]) -> dict[str, set[str]]:
        files: dict[str, set[str]] = {}
        for message in messages:
            files.setdefault(message["path"], set()).add(message["symbol"])
        return files
    was, now = by_file(before), by_file(after)
    return {path: sorted(symbols - was.get(path, set()))
            for path, symbols in sorted(now.items()) if symbols - was.get(path, set())}


@dataclass(frozen=True)
class Hit:
    """One line naming the old module; whether its document is a record; and whether the tool
    composed the line itself (a provenance docstring of the new package)."""

    path: str
    line: int
    text: str
    archived: bool
    composed: bool = False


def classify(root: Path, raw: list[str], origins: dict[str, list[Origin | None]]
             ) -> list[Hit]:
    """``git grep -n`` lines as hits; a document is ARCHIVED when it sits under an
    ``archive/`` or ``historical/`` directory or says ``ARCHIVED`` on its first line.

    A hit is COMPOSED when ``origins`` (each written file's verified line origins) says the
    writer composed its line: the package's own "Split from ..." provenance, which names the
    old path by design and is not a pointer to edit.  Judged by ORIGIN, never by path (the
    coordinator's ruling, 2026-10-15): a line inside the package that the source held, or
    that was written by hand, still counts, and a file whose origins did not verify has none
    to drop."""
    hits = []
    first_lines: dict[str, str] = {}
    for entry in raw:
        path, line, text = entry.split(":", 2)
        if path not in first_lines:
            with (root / path).open(encoding="utf-8") as handle:
                first_lines[path] = handle.readline()
        archived = bool({"archive", "historical"} & set(Path(path).parts)) or (
            "ARCHIVED" in first_lines[path])
        lines = origins.get(path, [])
        composed = 0 < int(line) <= len(lines) and lines[int(line) - 1] is None
        hits.append(Hit(path=path, line=int(line), text=text.strip(), archived=archived,
                        composed=composed))
    return hits
