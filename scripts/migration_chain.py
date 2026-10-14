"""
Shekel Budget App -- A migration file's revision ids, read from its text: the one parser.

Read from the text rather than by importing alembic, so it costs no app import
and works on a bare checkout and on a file read out of a commit.  Two readers:
``scripts/build_test_db_image.py``'s ``migration_head`` (the baked test image
is verified against the chain's head) and the release tool, ``tools/release``
(which migrations a release adds, edits or deletes, each named by its
revision).  Moved out of ``build_test_db_image.py`` unchanged by step X-cx's
L9a, so the two cannot read a revision two ways.

Stdlib only: ``build_test_db_image.py --print-tag``, which other worktrees'
prunes run, imports it.
"""
from __future__ import annotations

import re

#: The migration chain's directory, relative to the repository root: one
#: ``.py`` file per revision, directly inside it.
VERSIONS = "migrations/versions"

# ``revision = "<id>"``, annotated or not, at the start of a line.
_REVISION = re.compile(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", re.M)
# ``down_revision = "<id>"``, annotated or not, at the start of a line.  A merge
# migration's tuple of parents is not read, as it was not before the move.
_DOWN_REVISION = re.compile(r"^down_revision(?::[^=]+)?\s*=\s*['\"]([^'\"]+)", re.M)


def revision(text: str) -> str | None:
    """Return the revision id a migration file's text declares, or None when it
    declares none."""
    found = _REVISION.search(text)
    return found.group(1) if found else None


def down_revisions(text: str) -> list[str]:
    """Return every ``down_revision`` id a migration file's text declares, in order."""
    return _DOWN_REVISION.findall(text)
