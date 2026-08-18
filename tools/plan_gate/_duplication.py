"""The DUPLICATION arms: a registry's content is stated once (rule 16).

``_registry.py`` keeps each registry internally honest and ``_plan_gate.py``
keeps each arc document within its own shape.  Neither can see the failure this
module exists for: **a live document carrying a SECOND copy of what a registry
already states.**  Rules 1-15 all grade a claim where it lives, so a copy
somewhere else is invisible to every one of them -- and a copy is exactly what
goes stale, because nothing reconciles it.

All three arms were measured on 2026-08-11 and each had already produced a
wrong statement in a live document:

* **the ORDER** was restated in four documents besides ``steps.md``.  Two were
  stale: the balance README listed a SHIPPED step as pending work, and it
  stated that "``R6`` ships WITH X-an" -- contradicted by its own signpost, by
  the recurrence plan's section 0, by ``conventions.md`` rule 13 and by
  ``steps.md``.  One document disagreed with itself about the single thing a
  reader opens it for.
* **a registry's SIZE** was quoted in a document that does not own it: "98 open
  findings" against a ledger standing at 166 rows, inside a section citing a
  Section 6 whose table had moved out entirely.  The paragraph doing the
  counting NAMED the blind spot -- "drift the gate could not see" -- and kept
  the number, which is what a discipline without a predicate is worth.
* **a RETIRED section** sat live-looking in the credit-card plan from
  2026-07-19 to 2026-08-11, annotated as discharged only by a paragraph in a
  DIFFERENT document.  That is the placement rule 15 refuses: the notice must
  be on the artifact, because a grep reaches the section without the other
  document's help.  Two of the ids it ordered (``C8``, ``D1``) have since been
  reused by LIVE steps in other arcs.

**The remedy every message names is a POINTER, never a summary.**  A summary is
a second copy that has merely been shortened, and it goes stale on the same
commit the original moves.

Scanning notes, each carrying a false positive it prevents:

1. **Fenced regions are blanked** (:func:`_plan_gate._blank_fenced_regions`).
   The recurrence plan's section 0 holds a ``text`` fence listing step ids
   beside file counts, which is a MEASUREMENT and not a sequence.
2. **A table row is split into CELLS.**  A signpost row holds a summary in one
   cell and a pointer in the next; two step ids in two different cells are two
   statements, not a chain.
3. **Double-quoted spans are blanked.**  ``conventions.md`` must quote the
   wording it grades -- rule 3 cites "112 steps, 96 open" as the stale text it
   caught -- and a rule quoting a defect is not committing it.
4. **A chain may not cross a sentence boundary.**  Two steps named in
   consecutive sentences are two claims; the ordering ones observed all sit in
   one sentence, however long the parentheticals get.
"""
from __future__ import annotations

import re
from pathlib import Path

import _registry as registry
from _tables import UNESCAPED_PIPE_RX
from _plan_gate import _blank_fenced_regions

def live_docs() -> dict[str, Path]:
    """Return every live planning document, by the name the arms report it under.

    **A function rather than a module constant**, and for the reason the whole
    module exists: a dict built at import time is a COPY of
    :data:`_registry.ARC_DOCS`, so the control fixtures that re-point that map
    would grade the real file while believing they had staged a defect -- a
    control that cannot fail, which is the failure mode
    ``ticked_entry_violations`` shipped with and rule 16 is about.

    Returns:
        The document map: registry name or arc slug to path.
    """
    return {
        "ledger": registry.LEDGER,
        "steps": registry.STEPS,
        "conventions": registry.CONVENTIONS,
        "verification": registry.PLANS / "verification.md",
        "lessons": registry.PLANS / "lessons.md",
        **registry.ARC_DOCS,
    }

#: Tokens that put two step ids in SEQUENCE.  Deliberately short: ``then`` and
#: an arrow are what an order restatement actually reads like, and every
#: looser candidate ("before", "after", "with") is ordinary argument prose --
#: "R6 reads a column R5 creates" must not fire, and a gate that cries wolf is
#: uninstalled rather than fixed.
_CONNECTIVE_RX = re.compile(r"(?:\bthen\b|->|→|\bfollowed by\b)", re.IGNORECASE)

#: A sentence boundary: terminal punctuation followed by whitespace and a
#: capital or a bold/backtick opener.  Used to bound a chain, not to split
#: prose, so it is deliberately conservative.
_SENTENCE_BREAK_RX = re.compile(r"[.!?]\s+(?=[A-Z*`])")

#: A double-quoted span, blanked before scanning.  Straight and curly quotes
#: both appear in the corpus.
#:
#: **It spans NEWLINES, and that is a fix rather than a convenience.**  These
#: documents are hard-wrapped at 100 columns, so the wrap point falls wherever
#: the prose puts it: ``rumdl fmt`` moved one live citation to read ``read "The
#: \\n ledger stands at 166 rows"``, the quotes stopped pairing on one line, and
#: the count arm reported the rules file for a count it was CITING.  A
#: line-bounded span makes the exemption depend on where a formatter wrapped.
#: The length bound keeps one unmatched quote from swallowing the rest of a
#: document and blanking real claims with it.
_QUOTED_RX = re.compile(r"\"[^\"]{0,400}\"|“[^”]{0,400}”")

#: The registry self-count shapes rule 3 grades, as they would read in a
#: document that does NOT own them.  Each was observed in the live corpus or is
#: the exact wording rule 3 names.
_COUNT_SHAPES: tuple[tuple[str, str], ...] = (
    (r"\b\d+\s+open\s+findings?\b", "ledger.md"),
    (r"\bledger\s+stand(?:s|ing)\s+at\s+\d+\b", "ledger.md"),
    (r"\b\d+\s+of\s+(?:the\s+)?ledger(?:'s)?\s+\d+\s+rows?\b", "ledger.md"),
    (r"\b\d+-row\s+(?:ledger|findings?\s+table)\b", "ledger.md"),
    (r"\b\d+\s+rows?\s+(?:live\s+)?in\s+ledger\.md\b", "ledger.md"),
    (r"\b\d+\s+steps?,\s*\d+\s+open\b", "steps.md"),
    (r"\bholds\s+\d+\s+edges?\s+over\s+\d+\s+rows?\b", "steps.md"),
)

#: Which document legitimately states each shape, so the arm never grades a
#: registry against its own sentence.
_COUNT_OWNERS = {"ledger.md": "ledger", "steps.md": "steps"}

#: Words a section uses to declare itself dead.  A section that needs one of
#: these is a section that should have been ARCHIVED (rules 5 and 15); the arm
#: refuses the annotation rather than grading its wording.
_RETIREMENT_RX = re.compile(
    r"\b(?:DISCHARGED|SUPERSEDED|RETIRED|OBSOLETE)\b"
    r"|\bnever be re-read\b|\bno longer (?:governs|applies)\b|\bnot a live gate\b",
)

#: How far from a retirement word the arm looks for evidence that the SECTION
#: is its subject.  Without this, every ruling recorded as superseded -- "R-R3's
#: subtype is SUPERSEDED by R-R13" -- would be read as a dead section, and the
#: rulings tables are full of them by design.
#:
#: **A DEMONSTRATIVE is required, not the bare word.**  Matching ``section``
#: alone read "see section 0" as a subject and reported two clean documents:
#: a POINTER to a section is the commonest thing said about one, and it is the
#: opposite of declaring it dead.
_SELF_REFERENCE_RX = re.compile(
    r"\b(?:this|the above|the below|the preceding|the following)\s+section\b"
    r"|\bsections?\s+(?:above|below)\b",
    re.IGNORECASE,
)
_SELF_REFERENCE_WINDOW = 80

#: The widest gap, in characters, between two chained step ids.  The real
#: restatements carry long parentheticals -- the balance signpost's chain ran
#: 210 characters between two members, listing what the first step closes --
#: so a tight bound would miss them.  Without ANY bound a stray ``then``
#: anywhere in a 2,000-character specification chains two ids that are merely
#: both mentioned in it, which is three of the four hits this arm reported on
#: its first run.
_MAX_CHAIN_GAP = 300


def _name(path: Path) -> str:
    """Return *path* relative to the repo, or its bare name when it is outside.

    The control fixtures stage a defect in a COPY under ``tmp_path``, which
    ``Path.relative_to`` refuses with a ``ValueError`` -- so a message built the
    obvious way crashes every control instead of reporting the violation it was
    written to prove.

    Args:
        path: The document being graded.

    Returns:
        A repo-relative path, or the file name when it is not under the repo.
    """
    try:
        return str(path.relative_to(registry.REPO))
    except ValueError:
        return path.name


def _scannable(text: str) -> str:
    """Return *text* with fences and quoted spans blanked, lengths preserved.

    Blanking rather than deleting keeps every offset valid, so a match's
    position still points into the original document.

    **NEWLINES inside a blanked span survive.**  A quoted citation may now span
    a wrapped line, and replacing its newline with a space would weld two
    paragraphs into one -- which is what :func:`_units` splits on, so the arms
    would then chain step ids across a paragraph break that a reader can see.

    Args:
        text: The whole document.

    Returns:
        The document with fenced and quoted regions replaced by spaces,
        line structure intact.
    """
    fenced = _blank_fenced_regions(text)
    return _QUOTED_RX.sub(
        lambda m: "".join("\n" if c == "\n" else " " for c in m.group(0)), fenced,
    )


def _units(text: str) -> list[str]:
    """Return the independent statements of *text*, in order.

    A table ROW is split into its cells and each becomes its own unit, because
    two adjacent cells are two statements: a signpost row carries a summary in
    one and a pointer in the next.  Everything else is grouped into
    blank-line-separated paragraphs.

    Args:
        text: The document, already passed through :func:`_scannable`.

    Returns:
        One string per independent statement.
    """
    units: list[str] = []
    paragraph: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            if paragraph:
                units.append(" ".join(paragraph))
                paragraph = []
            units.extend(UNESCAPED_PIPE_RX.split(line))
            continue
        if line.strip():
            paragraph.append(line.strip())
        elif paragraph:
            units.append(" ".join(paragraph))
            paragraph = []
    if paragraph:
        units.append(" ".join(paragraph))
    return units


def _known_step_idents() -> set[str]:
    """Return every bare step id ``steps.md`` indexes.

    Bare rather than ``arc:id`` because that is how the documents cite them:
    the arc is a COLUMN in the registry (rule 10) and prose writes ``X-f4``.

    Returns:
        The set of ids, including every alias an identity class carries.
    """
    idents = set()
    for row in registry.step_rows():
        idents.add(row.ident)
        for key in row.alias_keys():
            idents.add(key.split(":", 1)[-1])
    return idents


def _step_positions(unit: str, idents: set[str]) -> list[tuple[int, int, str]]:
    """Return ``(start, end, id)`` for every known step id named in *unit*.

    Matched on word boundaries so ``X-f4`` inside ``X-f4b`` is not a hit, and
    against the KNOWN set so an ordinary word shaped like an id cannot chain.

    Args:
        unit: One independent statement.
        idents: Every id ``steps.md`` knows.

    Returns:
        The hits, in document order.
    """
    hits = []
    for match in re.finditer(r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)"
                             r"(?![A-Za-z0-9-])", unit):
        if match.group(1) in idents:
            hits.append((match.start(1), match.end(1), match.group(1)))
    return hits


def _chain(unit: str, idents: set[str]) -> list[str] | None:
    """Return the ordered step ids chained in *unit*, or ``None``.

    A CHAIN is two or more known step ids with an ordering connective between
    consecutive members and no sentence boundary crossed.  The connective must
    sit strictly between the two ids: "X-f4 deletes it, then X-f5 posts" chains;
    "R6 reads a column R5 creates" does not.

    **Consecutive members must DIFFER, and the gap between them is bounded.**
    Both conditions came from measurement: without the first, a specification
    naming ``X-j`` twice with a ``then`` somewhere between reported the chain
    ``X-j -> X-j``, which orders nothing; without the second, any two ids in a
    long entry chained through a connective belonging to neither.

    Args:
        unit: One independent statement.
        idents: Every id ``steps.md`` knows.

    Returns:
        The chained ids in order when the unit restates a sequence, else
        ``None``.
    """
    hits = _step_positions(unit, idents)
    if len(hits) < 2:
        return None
    best: list[str] = []
    current: list[str] = [hits[0][2]]
    for index in range(1, len(hits)):
        gap = unit[hits[index - 1][1]:hits[index][0]]
        ident = hits[index][2]
        linked = (
            ident != current[-1]
            and len(gap) <= _MAX_CHAIN_GAP
            and _CONNECTIVE_RX.search(gap)
            and not _SENTENCE_BREAK_RX.search(gap)
        )
        if linked:
            current.append(ident)
        else:
            best = max(best, current, key=len)
            current = [ident]
    best = max(best, current, key=len)
    return best if len(best) >= 2 else None


def order_restatement_violations() -> list[str]:
    """Rule 16: the execution ORDER is ``steps.md``'s and no other document's.

    Returns:
        One message per live document restating a sequence of steps.
    """
    idents = _known_step_idents()
    problems = []
    for name, path in live_docs().items():
        if name == "steps" or not path.exists():
            continue
        for unit in _units(_scannable(path.read_text(encoding="utf-8"))):
            chained = _chain(unit, idents)
            if chained is None:
                continue
            problems.append(
                f"{_name(path)} restates the ORDER: "
                f"{' -> '.join(chained)}. conventions.md rule 16 -- the "
                "sequence is steps.md's alone, where it is graded against the "
                "dependency graph and recomputed on every commit. Replace this "
                "with a POINTER; a shortened copy is still a copy, and the two "
                "copies measured on 2026-08-11 were both stale. Keep the "
                "REASON a step sits where it does if it is this arc's argument."
            )
    return problems


def foreign_count_violations() -> list[str]:
    """Rule 16: a registry's own SIZE is stated only in that registry.

    Rule 3 grades each registry's self-count against its own table and can see
    nothing about a copy elsewhere.  The balance README's copy read 98 against
    a ledger of 166.

    Returns:
        One message per count stated outside the registry that owns it.
    """
    problems = []
    for name, path in live_docs().items():
        if not path.exists():
            continue
        text = _scannable(path.read_text(encoding="utf-8"))
        for shape, owner in _COUNT_SHAPES:
            if _COUNT_OWNERS.get(owner) == name:
                continue
            for match in re.finditer(shape, text, re.IGNORECASE):
                problems.append(
                    f"{_name(path)} states {owner}'s own "
                    f"size: {match.group(0).strip()!r}. conventions.md rule 16 "
                    f"-- that number is graded in {owner} against the table it "
                    "counts, and nothing reconciles a copy. Name the COMMAND "
                    "that measures it, or point at the registry."
                )
    return problems


def retired_section_violations() -> list[str]:
    """Rule 16: a live document may not declare one of its own sections dead.

    The remedy for a section that no longer governs is to ARCHIVE it under rule
    5, where rule 15's banner then applies -- not to annotate it in place and
    leave it looking live.  The credit-card plan's discharged sequencing sat
    live for three weeks with its retirement recorded in another file.

    A retirement word alone is not the trigger: rulings tables record superseded
    RULINGS by design.  The arm fires only when the word's neighbourhood names a
    section, which is what makes the section itself the subject.

    Returns:
        One message per self-declared dead section in a live document.
    """
    problems = []
    for path in live_docs().values():
        if not path.exists():
            continue
        text = _scannable(path.read_text(encoding="utf-8"))
        for match in _RETIREMENT_RX.finditer(text):
            window = text[
                max(0, match.start() - _SELF_REFERENCE_WINDOW):
                match.end() + _SELF_REFERENCE_WINDOW
            ]
            if not _SELF_REFERENCE_RX.search(window):
                continue
            problems.append(
                f"{_name(path)} declares one of its own "
                f"sections dead ({match.group(0)!r}). conventions.md rule 16 -- "
                "annotating a section as retired leaves it live-looking to "
                "every reader who arrives by grep. ARCHIVE it under rule 5, "
                "where rule 15 puts the banner on the artifact itself, and "
                "leave a pointer here."
            )
    return problems
