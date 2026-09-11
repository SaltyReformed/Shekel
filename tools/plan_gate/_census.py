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
disables against 16, ``X-ba`` said twenty-six id accessors against 27, ``X-be``
named a module at the 1000-line ceiling that had dropped to 857 while missing
one that had joined it, and ``X-i1`` counted FOUR context inputs, one of which
(``live_amount_overrides``) another step had already deleted -- ``0`` matches in
``app/`` and ``tests/``.  Every one of those rows was correctly OPEN; what had
rotted was what each said about the code.

**THIS DOCSTRING'S FIRST DRAFT CARRIED TWO WRONG NUMBERS ITSELF**, in a module
whose whole subject is stale counts: it said 17 rather than 16, and reported
``X-ah``'s 34 as stale against 36.  Both came from grepping a NAME, which counts
PROSE -- four docstrings that discuss the ``type=int`` census, and one narrating
a ``duplicate-code`` disable ``X-au-d`` had removed.  ``X-ah``'s row was right
all along.  Nothing grades a docstring, which is exactly why the ``code`` and
``comments`` filters below exist and why this paragraph is here rather than a
silent edit.

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

import functools
import io
import re
import tokenize
from pathlib import Path

import _registry as registry
import _rulings as rulings

#: Cited by every message below, so a failure sends the reader to the rule.
_RULE = "conventions.md rule 6"

#: The marker, as it reads inline in a row:
#:
#:     (census 16 comments `pylint: *disable=[^#]*duplicate-code` in `app/**/*.py`)
#:
#: The UNIT is ``lines`` or ``files``, because a census says one or the other
#: and conflating them is how "20 branches in 12 modules" becomes one number.
#: An optional FILTER, ``code`` or ``comments``, precedes it and restricts the
#: walk to that token class -- a name-grep counts PROSE otherwise, which is what
#: made the first two markers written under this rule both wrong.
#:
#: **Any WHITESPACE separates the tokens, including a newline**, because these
#: documents are reflowed by ``rumdl fmt`` to 100 columns and a marker that
#: demanded single spaces became unreadable the moment a formatter wrapped it.
#: Two gates were fighting over one line; this is which of them gave way, and it
#: is the one whose requirement was arbitrary.  The count
#: lives INSIDE the marker so that the number and the thing that checks it
#: cannot drift apart -- a number in the prose beside one would be rule 14's
#: two homes again.
MARKER = re.compile(
    r"\(census\s+(?P<count>\d+)\s+(?:(?P<filter>code|comments)\s+)?(?P<unit>lines|files)\s+"
    r"`(?P<pattern>[^`]+)`\s+in\s+`(?P<glob>[^`]+)`\)",
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


@functools.lru_cache(maxsize=None)
def _python_text(source: str, mode: str) -> str:
    """Return the source with everything but one token class blanked out.

    **A name-grep cannot tell code from prose**, which `lessons.md` records as a
    paid-for lesson and which the first two markers written under rule 6 both
    walked into: ``X-ah``'s census of ``type=int`` counted four DOCSTRINGS that
    discuss the census, and ``X-al``'s census of ``duplicate-code`` disables
    counted a docstring narrating a disable that had been REMOVED.  Both read as
    precise and both were wrong, in the direction that invents work.

    ``code`` blanks COMMENT and STRING spans, so a docstring mentioning a symbol
    is not a use of it and a trailing comment cannot satisfy a census of the
    statement beside it.  ``comments`` blanks everything else, which is what a
    census of ``# pylint: disable=...`` directives actually means -- the
    directive IS a comment, and prose about one is a string.

    **Spans are blanked rather than lines dropped**, so line NUMBERS are
    preserved: the count stays "matching lines", the same unit ``lines`` uses,
    and two identical statements in one file still count twice.

    Returns:
        The source with the unwanted token spans replaced by spaces; the source
        unchanged when it will not tokenize, so a syntax error degrades to the
        ``lines`` behaviour rather than silently reporting zero.
    """
    lines = source.splitlines()
    wanted = (tokenize.COMMENT,) if mode == "comments" else (tokenize.STRING, tokenize.COMMENT)
    spans = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            hit = token.type in wanted
            if hit if mode == "code" else not hit:
                spans.append(token.start + token.end)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source
    for srow, scol, erow, ecol in spans:
        for row in range(srow, min(erow, len(lines)) + 1):
            if row - 1 >= len(lines):
                continue
            line = lines[row - 1]
            lo = scol if row == srow else 0
            hi = ecol if row == erow else len(line)
            lines[row - 1] = line[:lo] + " " * max(0, hi - lo) + line[hi:]
    return "\n".join(lines)


@functools.lru_cache(maxsize=None)
def _read_at(path: Path, _mtime_ns: int, _size: int) -> str:
    """Read one file, memoized on its IDENTITY rather than its name.

    The arm walks the same trees once per MARKER -- thirty of them over 533
    files in ``app/`` -- and tokenizing each file per marker took the gate from
    90 seconds to 250.  A gate slow enough to skip is a gate that gets skipped.

    **The mtime and size are in the key deliberately.**  :func:`census_paths`
    and :func:`census_count` are PUBLIC so a control can measure, and the first
    control that plants a tree, measures, edits a planted file and measures
    again would otherwise read the old bytes and confirm a wrong number -- which
    is ``lessons.md``'s own recorded shape: *a census and a gate can be blind
    the same way, and then they confirm each other*.  A cache keyed on the path
    alone is safe only for as long as nobody writes such a control, which is not
    a property worth resting on.

    Retention is bounded by the corpus rather than by ``maxsize``: today's
    markers hold ``app/`` and ``tests/`` raw plus their filtered renderings,
    about 69 MB of strings, which is fine for a process that exits after one
    commit and is stated here so the next reader need not measure it again.
    """
    return path.read_text(encoding="utf-8")


def _read(path: Path) -> str:
    """Read one file through the identity-keyed cache."""
    stat = path.stat()
    return _read_at(path, stat.st_mtime_ns, stat.st_size)


def census_unreadable(paths: list[Path]) -> list[Path]:
    """Return the files the walk cannot decode.

    A skipped file makes the census UNDER-report while still reading as precise,
    which is the defect this whole module is about wearing different clothes.
    Nothing under the roots fails to decode today, so this reports rather than
    guesses.
    """
    bad = []
    for path in paths:
        try:
            _read(path)
        except (OSError, UnicodeDecodeError):
            bad.append(path)
    return bad


def census_count(pattern: re.Pattern[str], paths: list[Path], unit: str,
                 token_filter: str | None = None) -> int:
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
            text = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if token_filter:
            text = _python_text(text, token_filter)
        hits = sum(1 for line in text.splitlines() if pattern.search(line))
        total += min(hits, 1) if unit == "files" else hits
    return total


def _documents() -> list[Path]:
    """Every live document a census marker may appear in.

    **Rule 6 says the sentence binds EVERY ROW, so the arm reads every registry
    that HOLDS rows** -- not the three it started with.  `rulings.md` carries at
    least one census of its own (`R-R54`'s locale sites), and an arm scoped to
    some documents is rule 3's recorded failure verbatim: *"a rule stated for
    one artifact and graded on one artifact is a rule the second artifact does
    not have."*
    """
    return [
        registry.STEPS,
        registry.LEDGER,
        rulings.RULINGS,
        registry.PLANS / "conventions.md",
        registry.PLANS / "verification.md",
        registry.PLANS / "lessons.md",
        *registry.ARC_DOCS.values(),
    ]


#: A marker-shaped opening.  Anything starting ``(census `` that :data:`MARKER`
#: does NOT consume is a number wearing the gate's uniform: it reads as "no
#: census is claimed" and passes, which is rule 3's own recorded failure mode
#: (a pattern matching nothing reads as silence).  Three instances existed the
#: day this arm was written -- trailing prose inside the parenthesis, and a
#: marker line-WRAPPED between ``in`` and its glob, twice.
#: A DIGIT is required so rule 6's own grammar example, ``(census <N> ...)``,
#: is exempt by SHAPE rather than by an allowlist: a placeholder cannot be
#: mistaken for a count.
NEAR_MISS = re.compile(r"\(census \d")


def near_miss_violations() -> list[str]:
    """Refuse a marker-shaped string the parser cannot read.

    Returns:
        One message per ``(census `` opening :data:`MARKER` did not consume.
    """
    problems = []
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        consumed = {m.start() for m in MARKER.finditer(text)}
        for near in NEAR_MISS.finditer(text):
            if near.start() in consumed:
                continue
            line = text.count("\n", 0, near.start()) + 1
            problems.append(
                f"{document.name}:{line} opens a census the parser cannot read. "
                f"It must read `(census <N> [code|comments] lines|files `<regex>` "
                f"in `<glob>`)` with NOTHING between the glob and the closing "
                f"paren -- a marker the regex misses reads as no census at all "
                f"and its number is graded by nothing ({_RULE}). Line breaks "
                f"BETWEEN its tokens are fine; a formatter puts them there",
            )
    return problems


def census_violations() -> list[str]:
    """Re-run every census marker in the live documents and grade its number.

    Returns:
        One message per marker whose stated count is wrong, whose glob is
        illegal or matches nothing, or whose pattern will not compile.
    """
    problems: list[str] = []
    for document in _documents():
        for match in MARKER.finditer(document.read_text(encoding="utf-8")):
            stated, unit, token_filter = (
                int(match["count"]), match["unit"], match["filter"])
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
                # A marker lives in a markdown TABLE row, where a literal `|`
                # must be written `\|` or it splits the row -- and `re` reads
                # `\|` as a LITERAL pipe, so alternation was unusable and a
                # pattern needing it would have measured nothing and committed
                # green as `(census 0 ...)`.  This is the one place the two
                # grammars meet, so this is where the escape is undone.
                pattern = re.compile(match["pattern"].replace(r"\|", "|"))
            except re.error as exc:
                problems.append(f"{where} will not compile: {exc} ({_RULE})")
                continue
            unreadable = census_unreadable(paths)
            if unreadable:
                problems.append(
                    f"{where} cannot read {len(unreadable)} of its files "
                    f"({unreadable[0].name} first).  A skipped file makes the "
                    f"count UNDER-report while reading as precise ({_RULE})",
                )
                continue
            measured = census_count(pattern, paths, unit, token_filter)
            if measured != stated:
                problems.append(
                    f"{where} says {stated} "
                    f"{(token_filter + ' ') if token_filter else ''}{unit} and the code "
                    f"holds {measured}.  "
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
    return sum(
        1 for d in _documents() if MARKER.search(d.read_text(encoding="utf-8"))
    )
