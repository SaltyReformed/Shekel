"""The ARCHIVE arm: a historical document declares itself one, on its first line.

**This module exists because a session read an archived implementation plan as
the plan of record.**  The document had been superseded months earlier and said
nothing about it, so nothing in the act of opening it was a warning, and the
work that followed was planned against a document the developer had deliberately
retired.

**Keeping live documents from CITING an archived path does not fix that**, which
is why the predicate is not written that way.  A grep, a glob or a
half-remembered filename reaches an archived file without any live document's
help, and the archive is precisely where a filename that once meant something
still resolves -- so a rule policing live citations would have been GREEN
throughout the incident it was meant to prevent.  The banner goes on the
ARTIFACT instead, at the first line, where a reader arriving from anywhere
cannot get around it.

Separate from ``_registry.py`` because it grades neither registry: its subject
is every markdown file under an archived directory, and its only shared input is
the repository root (:data:`tools.ci.arcs.REPO`).  What makes a document
archived, the directory names and the banner, is :mod:`tools.ci.archive`'s, the
one home the split tool's census reads it from too.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from tools.ci import arcs
from tools.ci.archive import ARCHIVE_BANNER, declares_archived, in_archived_dir


def archived_docs() -> Iterator[Path]:
    """Every markdown file under an ``archive/`` or ``historical/`` directory, walked from
    ``docs`` (:func:`tools.ci.archive.in_archived_dir` keys on the directory name)."""
    for path in sorted((arcs.REPO / "docs").rglob("*.md")):
        if in_archived_dir(path.relative_to(arcs.REPO)):
            yield path


def archive_banner_violations() -> list[str]:
    """Rule 15: an archived document SAYS SO on its first line.

    Returns:
        One message per archived document that does not carry the banner.
    """
    problems = []
    for path in archived_docs():
        if not declares_archived(path.read_text()):
            problems.append(
                f"{path.relative_to(arcs.REPO)} is archived and its first "
                f"line does not say so.  It must open with {ARCHIVE_BANNER!r} "
                f"... so a reader who arrives by grep is told before they read "
                f"a word of it (conventions.md rule 15)",
            )
    return problems
