"""The database migrations a release adds, edits or deletes, each named by its revision.

What a release brings is ``<base>...<cut>``: every change on ``cut``'s side since it and
``base`` last met, so a commit ``base`` holds and ``cut`` lacks (a merge ``main`` took
that ``dev`` never did) is not read as a deletion.  A file directly inside the chain's
directory is one revision (:data:`scripts.migration_chain.VERSIONS`), and its id is read
by the one parser, :func:`scripts.migration_chain.revision`: from the cut for a file
added or edited, from ``base`` for a file deleted.  An EDITED or DELETED file is one
already on ``main``; production never re-runs it, so the approval question names it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.migration_chain import VERSIONS, revision
from tools.ci.gitcmd import git

ADDED, EDITED, DELETED = "ADDED", "EDITED", "DELETED"
#: ``git diff --name-status`` letters, with renames off: a type change edits the file.
_CHANGE = {"A": ADDED, "M": EDITED, "T": EDITED, "D": DELETED}


@dataclass(frozen=True)
class Migration:
    """One migration file a release changes: how, its path, and its revision id (None
    when the file declares none)."""

    change: str
    path: str
    revision: str | None


def migrations(root: Path, base: str, cut: str) -> tuple[Migration, ...]:
    """Every migration ``<base>...<cut>`` adds, edits or deletes, in path order.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the range or a file.
    """
    out = git(root, "diff", "--name-status", "-z", "--no-renames", f"{base}...{cut}", "--",
              VERSIONS)
    fields = out.split("\0")
    found = []
    # ``-z``: each change is its letter and its path, NUL-separated and never quoted, so a
    # name git would otherwise quote (a non-ASCII character) is read as written.
    for letter, path in zip(fields[0::2], fields[1::2]):
        if PurePosixPath(path).parent != PurePosixPath(VERSIONS) or not path.endswith(".py"):
            continue
        change = _CHANGE[letter]
        text = git(root, "show", f"{base if change == DELETED else cut}:{path}")
        found.append(Migration(change, path, revision(text)))
    return tuple(sorted(found, key=lambda migration: migration.path))
