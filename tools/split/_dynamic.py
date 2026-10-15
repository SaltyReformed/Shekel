"""The RUN half of the proof, as pure comparisons of what the runs observed: the old -> new
node-id map, the collection, every test's outcome, pylint's messages and the census of the
old module's name.  The runs themselves are :mod:`tools.split._run`'s; nothing here starts a
process, so every arm can be shown failing on a hand-built observation.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pylint.checkers.imports import MSGS as IMPORT_MESSAGES

from tools.ci.archive import declares_archived, in_archived_dir
from tools.split._layout import Layout
from tools.split._render import Mark, Origin, Provenance
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


#: The fixture scopes a package cuts: one instance per module, class or package becomes one
#: per part, per slice or per new package.
_CUT_SCOPES = frozenset({"module", "class", "package"})


def fixture_failures(observed: dict, source: str, source_file: str) -> list[str]:
    """Why the source's tests reach a fixture whose reach the split would change, read from
    every fixture its record run SET UP (``observed``'s merged ``setups``; the coordinator's
    ruling on the follow-up leaf's fork F, 2026-10-15).

    Refused: a fixture scoped to a module, class or package and defined outside the source
    file (``source_file``, resolved): a parent conftest's or a plugin's, whose one instance
    the split may cut into several (a class moved whole keeps its own, so this refuses more
    than it must, on the safe side).  And a fixture registered on the source module or a
    class in it (its ``baseid``) whose function is defined in another file, as one imported
    into the source by name is: it would reach only the part its import lands in (a fixture
    pytest generates for an xunit ``setup_method`` has this shape too and is refused with it;
    no test defines one, measured 2026-10-15).  A fixture never
    set up cannot change what a test does, so set-up observation is the property itself.  A
    test the run reported that no process's record ran is refused too: a worker whose file
    is missing would otherwise leave its fixtures unseen."""
    failures = []
    unseen = sorted(set(observed["outcomes"]) - set(observed["setups_ran"]))
    if unseen:
        failures.append(f"no process recorded the fixtures of {len(unseen)} test(s) the run "
                        f"reported, e.g. {unseen[:3]}")
    for setup in observed["setups"]:
        foreign = setup["file"] != source_file
        where = (f"fixture {setup['argname']} (scope {setup['scope']}, defined in "
                 f"{setup['file']}, set up for {setup['by']})")
        if setup["scope"] in _CUT_SCOPES and foreign:
            failures.append(f"{where}: its one instance per {setup['scope']} may be cut "
                            "into one per part")
        if foreign and (setup["baseid"] == source or setup["baseid"].startswith(source + "::")):
            failures.append(f"{where}: registered on the source but defined in another file, "
                            "as an imported fixture is, it would reach only the part its import "
                            "lands in")
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
                   origins: dict[str, list[Mark]], block: tuple[int, int]
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
            if not isinstance(origin, Origin):
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


def compare_importers(before: list[dict], after: list[dict], repointed: dict[str, set[int]]
                      ) -> tuple[list[str], list[str]]:
    """The re-pointed importers' pylint messages judged by ORIGIN: the failures, and the
    messages named (the coordinator's ruling on the follow-up leaf's fork B, 2026-10-15).

    An importer's only changed lines are its re-pointed import statements, ``repointed``
    (:func:`tools.split._prove.importer_lines` checks it against the record), so every other
    line is its own origin.  A message there is answered only by one of the same symbol on
    the same line before, each answering at most one; anything else is REFUSED, a change
    the split made to a module it did not move.  A message on a re-pointed statement that
    the line did not hold before is the re-point's own, the commit's deliberate edit, so it
    is NAMED in the proof block and not refused (test_salary's shekel-private-module-import on
    test_c46_except_narrowing.py:48 is the class the coordinator ruled at 11:08 and BAL-658
    settles).  The count per kind this replaces let a new message hide behind a vanished
    one of its kind."""
    held = Counter((m["path"], m["symbol"], m["line"]) for m in before)
    failures, named = [], []
    for message in sorted(after, key=lambda m: (m["path"], m["line"], m["symbol"])):
        path, line, symbol = message["path"], message["line"], message["symbol"]
        if held[(path, symbol, line)] > 0:
            held[(path, symbol, line)] -= 1
        elif line in repointed.get(path, set()):
            named.append(f"{path}:{line}: {symbol}")
        else:
            failures.append(f"{path}:{line}: {symbol}, which the importer's line {line} did "
                            "not hold before the split")
    return failures, named


@dataclass(frozen=True)
class Hit:
    """One line naming the old module; whether its document is a record; and whether it is
    the package's own provenance, a line the tool composed to name the source."""

    path: str
    line: int
    text: str
    archived: bool
    provenance: bool = False


def classify(root: Path, raw: list[tuple[str, int, str]],
             origins: dict[str, list[Mark]]) -> list[Hit]:
    """The census's ``(path, line, text)`` lines as hits.

    A document is ARCHIVED when it sits under an archived directory or opens with the
    archive banner, each read from its one home, the module the plan gate grades by
    (:mod:`tools.ci.archive`; the coordinator's ruling on the branch's neutral review, F3,
    2026-10-15).  ``ARCHIVED``
    anywhere on line 1, the rule this replaced, would have hidden a live module whose first
    line merely names an archived thing.

    A hit is PROVENANCE when ``origins`` (each written file's verified line marks) says the
    tool composed its line to name the source the package came from
    (:data:`tools.split._render.PROVENANCE`), which names the old path by design and is not
    a pointer to edit.  Judged by that mark alone (the coordinator's rulings, 2026-10-15: by
    origin, never by path; F8d and fork D, only the tool's provenance): a line of the
    package the source held, a plan's own docstring text, a slice's docstring, or a line
    written by hand still counts, and a file whose origins did not verify has none to
    drop."""
    hits = []
    records: dict[str, bool] = {}
    for path, line, text in raw:
        if path not in records:
            records[path] = in_archived_dir(PurePosixPath(path)) or declares_archived(
                (root / path).read_text(encoding="utf-8"))
        lines = origins.get(path, [])
        provenance = 0 < line <= len(lines) and isinstance(lines[line - 1], Provenance)
        hits.append(Hit(path=path, line=line, text=text.strip(), archived=records[path],
                        provenance=provenance))
    return hits
