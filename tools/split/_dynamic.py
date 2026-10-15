"""The RUN half of the proof, as pure comparisons of what the runs observed: the old -> new
node-id map, the collection, every test's outcome, pylint's messages and the census of the
old path.  The runs themselves are :mod:`tools.split._run`'s; nothing here starts a process,
so every arm can be shown failing on a hand-built observation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tools.split._layout import Layout
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


def compare_pylint(before: list[dict], after: list[dict]) -> list[str]:
    """Failures where the parts hold a pylint message kind the source did not, or more of
    one kind than it did.  duplicate-code is not counted here: the source's count of it is
    structurally zero (pylint never compares a file with itself), so each pair between parts
    is judged revealed or created by :mod:`tools.split._similar` instead."""
    was, now = pylint_counts(before), pylint_counts(after)
    return [f"{symbol}: {now[symbol]} after, {was[symbol]} before"
            for symbol in sorted(now) if symbol != DUPLICATE_CODE and now[symbol] > was[symbol]]


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
    """One line naming the old path, and whether its document is a record."""

    path: str
    line: int
    text: str
    archived: bool


def classify(root: Path, raw: list[str]) -> list[Hit]:
    """``git grep -n`` lines as hits; a document is ARCHIVED when it sits under an
    ``archive/`` or ``historical/`` directory or says ``ARCHIVED`` on its first line."""
    hits = []
    first_lines: dict[str, str] = {}
    for entry in raw:
        path, line, text = entry.split(":", 2)
        if path not in first_lines:
            with (root / path).open(encoding="utf-8") as handle:
                first_lines[path] = handle.readline()
        archived = bool({"archive", "historical"} & set(Path(path).parts)) or (
            "ARCHIVED" in first_lines[path])
        hits.append(Hit(path=path, line=int(line), text=text.strip(), archived=archived))
    return hits
