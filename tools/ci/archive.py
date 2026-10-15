"""What makes a document a HISTORICAL RECORD that governs nothing: ONE home for the rule.

A document is archived when it sits under an ``archive/`` or ``historical/`` directory, and
an archived document says so on its first line, with :data:`ARCHIVE_BANNER`.  Two tools read
the rule.  The plan gate grades it (conventions.md rule 15:
``tools/plan_gate/_archive.py``), and the split tool's census
(:func:`tools.split._dynamic.classify`) treats a line in such a document as a record, which a
split never edits.  Both now read it here.

The rule lived in the plan gate's private ``_archive.py`` until the split tool needed it
(the coordinator's ruling on the split branch's neutral review, F3, 2026-10-15).  The split
tool had spelled it a second way: ``ARCHIVED`` anywhere on line 1, which would have hidden a
live test module whose first line names an archived definition.  So the leaf moved here
rather than being copied.  It lives beside CI because it outlives the plan gate: X-cx's
cutover (L8) deletes the gate's plan arms, and the archived documents stay in the
repository (the reasoning :mod:`tools.ci.arcs` gives for its table).
"""
from __future__ import annotations

from pathlib import PurePath

#: Directory names whose contents are a historical record and govern nothing.
#: Three separate trees hold them -- ``docs/audits/**/archive``,
#: ``docs/historical`` and ``docs/plans/historical`` -- so a walk keys on the
#: directory NAME.  A walk rooted at any one arc's archive would miss the other
#: two, and the document that caused this rule was reachable from none of them.
ARCHIVED_DIRS = frozenset({"archive", "historical"})

#: What every archived document must open with.
ARCHIVE_BANNER = "> **ARCHIVED."


def in_archived_dir(path: PurePath) -> bool:
    """Whether the repository-relative ``path`` sits under an archived directory, at any
    depth."""
    return bool(ARCHIVED_DIRS & set(path.parts))


def declares_archived(text: str) -> bool:
    """Whether a document's ``text`` opens with :data:`ARCHIVE_BANNER`: its first line,
    leading blank lines and whitespace skipped, STARTS with the banner."""
    first = text.lstrip().splitlines()[:1]
    return bool(first) and first[0].startswith(ARCHIVE_BANNER)
