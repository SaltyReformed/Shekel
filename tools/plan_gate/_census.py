"""The arm that RUNS a planning document's census instead of trusting its number.

**A count in a planning row is a stored copy of a derived value beside no
reconciler**, which is the root cause three of this project's arcs exist to
remove, sitting in the documents that describe the removal.  ``CLAUDE.md`` rule
14 states it generally; ``conventions.md`` rule 6 already states the remedy for
ONE section -- *the row names the command rather than a copy of its answer* --
and rule 6 is scoped to the "where this stands" signpost while the same defect
lives in every row that carries a number.

**Measured before the rule was written**, so the rule is not a guess: of the
censuses in live rows on 2026-09-11, ``X-al`` said fifteen ``duplicate-code``
disables against 17, ``X-ah`` said 34 ``type=int`` sites against 36, ``X-bm``
said seven callers lacked ``pricing_load_options`` against 5, ``X-be`` named a
module at the 1000-line ceiling that had dropped to 857 while missing one that
had joined it, and ``X-i1`` counted FOUR context inputs, one of which
(``live_amount_overrides``) another step had already deleted -- ``0`` matches in
``app/`` and ``tests/``.  Every one of those rows was correctly OPEN; what had
rotted was what each said about the code.

**The prior art is the corpus's own**, at
``implementation_plan_pay_calendar.md``: *"A list of line numbers in a planning
document cannot survive the code -- these had already drifted twice -- so what
this section keeps is the two greps that REGENERATE the census."*  That section
is right and it is the only one that does it.  This module is that practice made
a predicate.

**A marker names a PATTERN and a PATH, never a command, and that is a security
decision rather than a stylistic one.**  Executing a shell string out of a
markdown file would make every planning document a code-execution surface for
anything that can open a pull request.  The document supplies a regex and a
glob; this module supplies the walk, in-process, with :mod:`re`.  Nothing a
document says can run.
"""
from __future__ import annotations

import re
from pathlib import Path

import _registry as registry

#: Cited by every message below, so a failure sends the reader to the rule.
_RULE = "conventions.md rule 6"

#: The marker, as it reads inline in a row:
#:
#:     (census 17 lines `pylint: *disable=[^#]*duplicate-code` in `app/**/*.py`)
#:
#: ``lines`` counts matching LINES and ``files`` counts matching FILES, because
#: a census says one or the other and conflating them is how "20 branches in 12
#: modules" becomes one number.  The count lives INSIDE the marker so that the
#: number and the thing that checks it cannot drift apart -- a number in the
#: prose beside a marker would be rule 14's two homes again.
MARKER = re.compile(
    r"\(census (?P<count>\d+) (?P<mode>lines|files) "
    r"`(?P<pattern>[^`]+)` in `(?P<glob>[^`]+)`\)",
)

#: Where a census may look.  A glob escaping the repository, or reaching into
#: the planning documents to count itself, is refused rather than resolved.
_ROOTS = ("app/", "tests/", "scripts/", "tools/", "migrations/", "deploy/")


def census_paths(glob: str) -> list[Path] | None:
    """Return the files a census glob names, or ``None`` if the glob is illegal.

    Public because a CONTROL must be able to measure: staging a marker that is
    expected to PASS means deriving the true figure the same way the arm does,
    and a control that hardcoded one would fail on every correct edit to
    ``app/`` -- the pinned-data failure ``_staging`` records four times over.
    """
    if glob.startswith("/") or ".." in glob or not glob.startswith(_ROOTS):
        return None
    # Defence in depth rather than a live concern: no symlink exists under the
    # census roots today and CPython's ``**`` does not descend into one, so the
    # resolve check is what keeps that TRUE rather than merely true now.  A
    # census that could follow a link out of the tree would read whatever the
    # link pointed at and report a number about it.
    root = registry.REPO.resolve()
    return sorted(
        p for p in registry.REPO.glob(glob)
        if p.is_file() and p.resolve().is_relative_to(root)
    )


def census_count(pattern: re.Pattern[str], paths: list[Path], mode: str) -> int:
    """Return matching lines, or matching files, across *paths*.

    **The walk is LINE-based**, so a pattern may not span lines: a census of
    ``request.args.get(..., type=int)`` written as one expression would silently
    miss the calls a formatter has wrapped, which is a census that under-reports
    while reading as precise.  Write the pattern against the token that survives
    wrapping -- ``type=int`` rather than the whole call -- and say in the row
    what the token stands for.

    Public for the reason :func:`census_paths` is.
    """
    total = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = sum(1 for line in text.splitlines() if pattern.search(line))
        total += min(hits, 1) if mode == "files" else hits
    return total


def census_violations() -> list[str]:
    """Re-run every census marker in the live documents and grade its number.

    Returns:
        One message per marker whose stated count is wrong, whose glob is
        illegal or matches nothing, or whose pattern will not compile.
    """
    problems: list[str] = []
    documents = [registry.STEPS, registry.LEDGER, *registry.ARC_DOCS.values()]
    for document in documents:
        for match in MARKER.finditer(document.read_text(encoding="utf-8")):
            stated, mode = int(match["count"]), match["mode"]
            where = f"{document.name}: census `{match['pattern']}` in `{match['glob']}`"
            paths = census_paths(match["glob"])
            if paths is None:
                problems.append(
                    f"{where} names a path outside {' / '.join(_ROOTS)}.  A census "
                    f"reads the CODE, and a glob that escapes the tree is not one "
                    f"({_RULE})",
                )
                continue
            if not paths:
                problems.append(
                    f"{where} matches no file at all.  A census over nothing "
                    f"reports 0 and reads as a closed finding ({_RULE})",
                )
                continue
            try:
                pattern = re.compile(match["pattern"])
            except re.error as exc:
                problems.append(f"{where} will not compile: {exc} ({_RULE})")
                continue
            measured = census_count(pattern, paths, mode)
            if measured != stated:
                problems.append(
                    f"{where} says {stated} {mode} and the code holds {measured}.  "
                    f"A census is RE-RUN, never remembered: this row has been "
                    f"telling a reader something about the code that stopped "
                    f"being true ({_RULE})",
                )
    return problems


def documents_carrying_a_census() -> int:
    """Return how many live documents carry at least one census marker.

    The convention is young, so the arm above grades whatever exists and this
    is what a control uses to assert it is grading SOMETHING -- a pattern that
    matches nothing reads as "no census is claimed" and passes, which is rule
    3's own recorded failure mode one register down.
    """
    documents = [registry.STEPS, registry.LEDGER, *registry.ARC_DOCS.values()]
    return sum(
        1 for d in documents if MARKER.search(d.read_text(encoding="utf-8"))
    )
