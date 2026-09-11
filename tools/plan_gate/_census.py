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
import sys
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


#: The interpreter a FILTERED census can be run on.  **PEP 701 (Python 3.12)
#: stopped emitting an f-string as one ``STRING`` token** and split it into
#: ``FSTRING_START`` / ``FSTRING_MIDDLE`` / the interpolated expression's own
#: tokens / ``FSTRING_END``.  That split is what the filters below need, and
#: wanting it is not a preference: ``f"<TransferTemplate ... ${self.default_amount}>"``
#: is a REAL read of the column, so a step deleting the column must see it,
#: while ``f"... carries no due_date, so there is no date to "`` is prose that
#: must not be counted.  A pre-3.12 tokenizer can tell neither apart -- it hides
#: both inside one ``STRING`` -- so it is reported as blindness rather than
#: guessed around.  ``CLAUDE.md`` states the project's floor as 3.12+ and the
#: Dockerfile and ``ci.yml`` both run 3.14, so this states an existing
#: requirement rather than adding one.
#:
#: **Written after the divergence shipped**, so it is not a guess: the first
#: draft of this module was measured on 3.11 and its numbers were committed;
#: CI re-ran them on 3.14 and three markers disagreed, because on 3.14 the
#: interpolations counted (right, and 3.11 missed them) and the literal text
#: counted too (wrong, and 3.11 got it right).  One arm, two answers, and the
#: gate would have graded whichever interpreter reached it first.
_TOKENIZER_FLOOR = (3, 12)

#: Every token class that is literal string TEXT rather than code, read out of
#: this interpreter's own token table BY SHAPE rather than listed.  Before PEP
#: 701 that was ``STRING`` alone; since 3.12 an f-string arrives as its own
#: START / MIDDLE / END wrapped around the tokens of each interpolation, and
#: 3.14 adds the same three for a t-string.
#:
#: **A written-out list would have a floor and no CEILING.**
#: :data:`_TOKENIZER_FLOOR` closes the divergence downward; a later Python that
#: adds another string-literal family would reopen it upward, silently, and one
#: arm with two answers is this module's whole subject.  Deriving the set
#: deletes the allowlist the design doctrine calls scaffolding.  Measured
#: identical to the seven-name list on 3.11, 3.12, 3.13 and 3.14.
#:
#: **START and END are blanked alongside MIDDLE rather than left standing.**
#: The rule is *the whole literal is string text except the expressions inside
#: it*, so a census of ``f"`` finds a prefix on no interpreter; leaving the
#: delimiters would let one pattern answer differently per version.
#:
#: **One limit, measured rather than assumed.**  CPython emits ``{{`` and
#: ``}}`` as a token whose TEXT is the single unescaped brace while its span
#: starts at the source offset, so the blank falls one column short and a bare
#: brace survives: ``f"{{X}} and {X}"`` renders as ``   {   }        {X} ``.
#: Identical on 3.12, 3.13 and 3.14, and it can only ever OVER-count.  No live
#: marker contains a brace; one that did would need checking here first.
_STRING_TEXT = tuple(
    number for number, name in tokenize.tok_name.items()
    if name == "STRING" or name.endswith(("STRING_START", "STRING_MIDDLE", "STRING_END"))
)


def _blindness(subject: str) -> str:
    """Return the one sentence every path says when this tokenizer is too old.

    ONE spelling, because a message duplicated across two call paths is the
    same defect this package grades the planning documents for.

    Args:
        subject: what cannot be answered, as the sentence's subject.

    Returns:
        The sentence, naming the floor, this interpreter, and why it matters.
    """
    floor = ".".join(str(part) for part in _TOKENIZER_FLOOR)
    return (
        f"{subject} needs Python {floor}+ to tell an f-string's interpolated "
        f"CODE from its literal PROSE (PEP 701); this is "
        f"{sys.version_info.major}.{sys.version_info.minor}.  Answering anyway "
        f"would give a different number than the 3.14 the Dockerfile and "
        f"`ci.yml` both run, which is exactly the drift this arm exists to "
        f"catch ({_RULE})"
    )


def interpreter_is_gradeable() -> bool:
    """Return whether this tokenizer can tell an interpolation from the prose round it.

    **A gate that silently passes when it cannot run is worse than no gate**, and
    ``_shipped.history_is_gradeable`` is the same predicate one register over:
    the arms report their own blindness and a control asserts the predicate is
    TRUE on the live tree, so an interpreter below :data:`_TOKENIZER_FLOOR`
    fails loudly instead of quietly grading a different corpus.
    """
    return sys.version_info >= _TOKENIZER_FLOOR


@functools.lru_cache(maxsize=None)
def _python_text(source: str, mode: str) -> str:
    """Return the source with everything but one token class blanked out.

    **A name-grep cannot tell code from prose**, which `lessons.md` records as a
    paid-for lesson and which the first two markers written under rule 6 both
    walked into: ``X-ah``'s census of ``type=int`` counted four DOCSTRINGS that
    discuss the census, and ``X-al``'s census of ``duplicate-code`` disables
    counted a docstring narrating a disable that had been REMOVED.  Both read as
    precise and both were wrong, in the direction that invents work.

    ``code`` blanks COMMENT spans and every string-literal span
    (:data:`_STRING_TEXT`), so a docstring mentioning a symbol is not a use of
    it and a trailing comment cannot satisfy a census of the statement beside
    it.  ``comments`` blanks everything else, which is what a
    census of ``# pylint: disable=...`` directives actually means -- the
    directive IS a comment, and prose about one is a string.

    **Spans are blanked rather than lines dropped**, so line NUMBERS are
    preserved: the count stays "matching lines", the same unit ``lines`` uses,
    and two identical statements in one file still count twice.

    **An f-string is split, not swallowed.**  Its literal text is string text
    and its interpolations are code, which is what :data:`_STRING_TEXT` and
    :data:`_TOKENIZER_FLOOR` between them say: ``f"{self.default_amount}"``
    counts under ``code`` and ``f"...no due_date, so there is no date to "``
    does not.

    **The rule is LEXICAL and the difference matters.**  ``code`` asks whether
    the name stands outside every string literal and comment.  It does NOT ask
    *would deleting this break the line*, and the two part company on a name
    used as a STRING: ``db.CheckConstraint("default_amount >= 0")`` and
    ``data["default_amount"]`` break exactly as hard and this filter sees
    neither.  A step censusing such a name owes a second, UNFILTERED marker or
    a sentence saying so -- `conventions.md` rule 6.  `X-bp`'s carries one, in
    its SPECIFICATION rather than its index row: the row sat at 384 of a
    400-character cap, which is rule 4 putting a specification where rule 4
    says specifications go.

    **The interpreter guard is at :func:`census_count`, not here**, first
    because that is the PUBLIC boundary and this is private, so a caller is
    refused at the door.  Memoization is the second reason and the narrower
    one: ``lru_cache`` does not cache exceptions, so a guard here would still
    raise on every cache MISS, but it is skipped entirely for a
    ``(source, mode)`` this process has already read -- which no control could
    then grade.  The floor it enforces is :data:`_TOKENIZER_FLOOR`.

    Returns:
        The source with the unwanted token spans replaced by spaces; the source
        unchanged when it will not tokenize, so a syntax error degrades to the
        ``lines`` behaviour rather than silently reporting zero.
    """
    lines = source.splitlines()
    wanted = (tokenize.COMMENT,) if mode == "comments" else (*_STRING_TEXT, tokenize.COMMENT)
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

    Raises:
        RuntimeError: when *token_filter* is set and this interpreter is below
            :data:`_TOKENIZER_FLOOR`.  Refusing is the point: answering is how
            three markers were measured on 3.11, committed green, and failed in
            CI on 3.14 against the same code.
    """
    if token_filter and not interpreter_is_gradeable():
        raise RuntimeError(_blindness(f"a `{token_filter}` census"))
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

    **On an interpreter below the floor this REPORTS rather than returns
    silence**, which is where it parts from ``_shipped.history_is_gradeable``.
    That arm can return nothing because a shallow checkout is a property of the
    RUN and one control names it.  Measured here on 3.11, both ways: under
    silence THREE controls that assert cleanliness PASS -- both
    ``test_the_live_corpus_is_clean`` and
    ``test_a_marker_a_formatter_wrapped_is_still_read`` -- so the gate reports
    clean while grading nothing, which is the exact defect
    :func:`interpreter_is_gradeable` exists to prevent.  Silence also fails
    EIGHT controls that ask this function for its glob, no-file and
    will-not-compile messages with a bare ``assert []``, sending their reader
    to the glob arm when the cause is their Python.  The finding is that the
    gate cannot be run, so the finding is what it says.

    Returns:
        One message per marker whose stated count is wrong, whose glob is
        illegal or matches nothing, or whose pattern will not compile -- or the
        single blindness message when this interpreter cannot be trusted to
        answer any of them.
    """
    if not interpreter_is_gradeable():
        return [_blindness("re-running the live censuses")]
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
