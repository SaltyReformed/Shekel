"""X-cx's migration, its CARDS' TEXT: each item's title, body and comment, and the census.

What each card says (the L7 design's draft 4 s.3):

- a **step**: its ``steps.md`` sentence; ``## Spec``; its entry in its arc document,
  with every paragraph the input file calls a ``note`` taken out FIRST, then DEDENTED by
  its checkbox line's indentation (a line still left of it is refused, V3-2) and the
  checkbox's box dropped, so ``- [ ] **X-h** ...`` opens as ``**X-h** ...``; then each
  steps-section paragraph the input file gives this card (R-BAL245's ``card-body``);
- a **finding**: its ``ledger.md`` cell;
- a **question**: ONLY the developer's question -- the row's note word for word, or the
  question the input file wrote for it (R-BAL241) -- so ``quill file ruling
  --from-question`` takes exactly that; the row's other cells go in ONE comment opening
  :data:`AS_FILED`;
- a **ruling**: its ``rulings.md`` cell.

Every card but a question ends with an **As filed block** (:func:`as_filed`): a fenced
quotation, word for word, of the row's cells the card does not carry as structure, and
first the registry and commit it was filed from.  A fence renders nothing and links no
``#N``; the links and labels govern, and the block is a quotation.

Each card is held to quill's one check (:func:`check.violations`) with ``changed`` =
{title} (draft 4 s.3): its kind, its one arc label and its short title.

**The census is computed from the BUILT bodies** (draft 4 s.11 V3-2): every non-blank
line of each arc's steps section is put in exactly one place -- on a card, a note that
moves nowhere, a shipped step's pointer, the card already filed for X-cx
(:data:`_migrate_source.MAPPED`, whose text is not rewritten), a heading, or a paragraph
kept for L8 by its disposition -- and each step card's body is read back
(:func:`step_regions`): its sentence and its As filed block must be exactly its row's,
and the NON-BLANK lines of its spec and of each given paragraph must be the lines put on
it, region by region and in order, none missing, none doubled and nothing else
(:func:`census`).  A non-blank line the assembly lost, misplaced or added is a refusal,
whatever the spans say.

**What the census does not grade** (review of PR #546, LOW 2): BLANK lines, so a
paragraph break the assembly dropped or added (two paragraphs merged, one split) passes
it; and the DEDENT, which the census reads through the same :func:`dedent` the builder
writes with, so a wrong one is wrong on both sides alike.  The dedent is graded by its own
tests; the stored text, every blank line and indent of it, by ``migrate verify``, which
holds each fetched body EQUAL to its built one (draft 4 s.5).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.ci.arc_steps import CHECKBOX_RX, Entry, fenced_lines
from tools.quill._migrate_input import Decisions
from tools.quill._migrate_source import (
    MAPPED,
    Item,
    Layout,
    Paragraph,
    Registries,
    Source,
    Spec,
    indentation,
    question_note,
)
from tools.quill.check import TITLE, Draft, card_title, normalized, violations

#: The line a question's As filed comment opens with, by which a run finds it (draft 4
#: s.4).
AS_FILED = "As filed in ledger.md"
SPEC_HEADING = "## Spec"
#: The heading over a steps-section paragraph given to one card (R-BAL245, ``card-body``).
BOUND_HEADING = "## From the arc document's steps section"
#: Each kind's registry, named in its As filed block.
REGISTRY = {"step": "steps.md", "finding": "ledger.md", "question": "ledger.md",
            "ruling": "rulings.md"}
_FENCE = "```"
#: How an As filed block's first line opens; ``migrate verify`` (X-cx's B2) reads it.
FILED_FROM = "filed from: "
#: How every As filed block opens, the fence and its first line's start: the mark of a
#: card the migration filed, whatever its title and labels say (:func:`holds_as_filed`).
AS_FILED_OPENING = f"{_FENCE}text\n{FILED_FROM}"
#: An As filed block's first line, its commit apart (:func:`masked`).
_FILED_LINE = re.compile(rf"^({re.escape(FILED_FROM)}\S+ at )\S+$", re.MULTILINE)
#: A bare ``#N`` in prose, which GitHub links to the tracker's card N (draft 4 s.3).
_BARE_NUMBER = re.compile(r"(?<![\w#&/])#\d+\b")
#: An inline code span: a run of backticks, then text, then a run of as many.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")

#: The census's places for a line (:func:`census`).
ON_A_CARD = "on a card"
A_NOTE = "a note, moving nowhere"
SHIPPED = "a shipped step's pointer, moving nowhere"
HEADING = "a heading"
NO_STEP = "a checkbox for no step"
FILED_ALREADY = "a step already filed as its card, whose text is not rewritten"


@dataclass(frozen=True)
class Built:
    """One card's text: its title, its body, and its comment (None for none)."""

    title: str
    body: str
    comment: str | None


def as_filed(registry: str, commit: str, cells: Sequence[tuple[str, str]]) -> str:
    """The As filed block: the registry and commit, then each cell by its column, fenced."""
    lines = [f"{FILED_FROM}{registry} at {commit}",
             *(f"{column}: {value}" for column, value in cells)]
    return "\n".join([_FENCE + "text", *lines, _FENCE])


def holds_as_filed(text: str) -> bool:
    """Whether ``text`` -- a card's body or a comment, read as :func:`check.normalized` reads
    it (a web form's CRLF as LF) -- holds an As filed block: the card is one the migration
    filed, whatever has become of its title and labels."""
    return AS_FILED_OPENING in normalized(text)


def masked(text: str) -> str:
    """``text`` as two runs compare it: :func:`check.normalized`, each As filed block's commit
    left out.  The commit is the one the card was written at, which a later run reading the
    same text at a later commit names otherwise (draft 4 s.5: the commit line is not
    compared, it must resolve)."""
    return _FILED_LINE.sub(r"\1<commit>", normalized(text))


def differences(built: Built, title: str, body: str, comment: str | None) -> list[str]:
    """The parts of a card that differ from ``built``, its text as the registries make it:
    ``title`` exactly; ``body`` and its As filed ``comment``, each :func:`masked`.  A
    ``comment`` of None is not compared: whether one is due is the caller's question (the
    plan writes a missing one; ``migrate verify`` must refuse a question without one).  A
    comment given where ``built`` has none is a difference."""
    found = [] if title == built.title else ["title"]
    found += [] if masked(body) == masked(built.body) else ["body"]
    if comment is not None and masked(comment) != masked(built.comment or ""):
        found.append("comment")
    return found


def dedent(line: str, indent: int, opener: bool) -> str:
    """One line of a step's entry as its card holds it: ``indent`` spaces taken off, and on
    the ``opener`` (the checkbox line) the box too; a blank line is empty.  The spec's
    builder and the census both read it, so the two cannot disagree on what a line
    becomes."""
    if not line.strip():
        return ""
    line = line[indent:]
    if opener:
        found = CHECKBOX_RX.match(line)
        line = line[found.start("step") - len("**"):]
    return line


def _cells(item: Item) -> list[tuple[str, str]]:
    """The cells an item's card quotes in its As filed block, by column."""
    row = item.row
    if item.kind == "step":
        return [("id", row.ident), ("also", row.aliases), ("starts", row.blocked)]
    if item.kind == "ruling":
        return [("id", row.ident), ("also", row.also), ("date", row.date)]
    finding = [("finding", row.finding)] if item.kind == "question" else []
    return [("id", row.ident), ("also", row.also), *finding, ("worst measured", row.worst),
            ("status", row.status), ("owner", row.owner)]


def _note_lines(spec: Spec, decisions: Decisions) -> set[int]:
    """The lines of ``spec``'s entry the input file calls a note: they move nowhere."""
    return {index for paragraph in spec.paragraphs
            if decisions.notes.get((paragraph.arc, paragraph.step, paragraph.first_line))
            == "note" for index in range(paragraph.start, paragraph.stop)}


def _spec_text(item: Item, spec: Spec, lines: Sequence[str], decisions: Decisions,
               refusals: list[str]) -> str:
    """A step's spec as its card holds it: notes out first, then dedented, box dropped."""
    notes = _note_lines(spec, decisions)
    kept = [index for index in range(spec.entry.start, spec.entry.stop) if index not in notes]
    left = [index + 1 for index in kept
            if lines[index].strip() and indentation(lines[index]) < spec.indent]
    if left:
        refusals.append(f"{item.key}: lines {left} of its arc document sit left of its "
                        "checkbox once its notes are taken out (V3-2): call each one's "
                        "paragraph a note, or indent it")
    return "\n".join(dedent(lines[index], spec.indent, index == spec.entry.start)
                     for index in kept).strip("\n")


@dataclass(frozen=True)
class Inputs:
    """What the cards' text is built from: the registries, what they hold
    (:func:`_migrate_source.read_source`) and what the input file decides."""

    source: Source
    registries: Registries
    decisions: Decisions


def build(inputs: Inputs) -> tuple[dict[str, Built], list[str]]:
    """Every item's card text, by key, and what is refused in it."""
    refusals = []
    bound = defaultdict(list)
    for paragraph in inputs.source.preambles:
        given = inputs.decisions.preambles.get((paragraph.arc, paragraph.first_line))
        if given is not None and given.disposition == "card-body":
            bound[given.fields["step"]].append(paragraph)
    built = {item.key: _built(item, inputs, bound[item.key], refusals)
             for item in inputs.source.items if item.key not in MAPPED}
    return built, refusals


def _built(item: Item, inputs: Inputs, bound: Sequence[Paragraph],
           refusals: list[str]) -> Built:
    """One item's card text (the module docstring), held to quill's check with only its
    title written; ``bound``: the steps-section paragraphs given to its card."""
    cells = _cells(item)
    if any(_FENCE in value for _, value in cells):
        refusals.append(f"{item.key}: a cell holds {_FENCE}, which would end its As filed "
                        "block early")
    block = as_filed(REGISTRY[item.kind], inputs.registries.commit, cells)
    comment = None
    if item.kind == "step":
        spec = inputs.source.specs.get(item.key)
        text = "" if spec is None else _spec_text(
            item, spec, inputs.registries.documents[item.arc].splitlines(), inputs.decisions,
            refusals)
        parts = [item.row.title, SPEC_HEADING, text]
        for paragraph in bound:
            parts += [BOUND_HEADING, paragraph.text]
        body = "\n\n".join([*parts, block])
    elif item.kind == "question":
        body = inputs.decisions.questions.get(item.key, question_note(item.row) or "")
        comment = f"{AS_FILED}\n\n{block}"
    else:
        body = f"{item.row.finding if item.kind == 'finding' else item.row.rule}\n\n{block}"
    title = card_title(item.alias, inputs.decisions.names.get(item.key, ""))
    refusals += [f"{item.key}: {problem}" for problem in violations(
        Draft(item.kind, title, body, item.labels), {TITLE})]
    return Built(title, body, comment)


def bare_numbers(built: Built) -> int:
    """How many bare ``#N`` a card's title, body and comment hold outside fenced blocks and
    code spans: each would link the tracker's card N (draft 4 s.3)."""
    count = 0
    for text in (built.title, built.body, built.comment or ""):
        lines = text.splitlines()
        count += sum(len(_BARE_NUMBER.findall(_CODE_SPAN.sub("", line)))
                     for line, fenced in zip(lines, fenced_lines(lines)) if not fenced)
    return count


def step_regions(body: str) -> tuple[str, list[str], list[list[str]], str] | None:
    """A step card's body read back (:func:`build`'s shape): what precedes
    :data:`SPEC_HEADING` (its sentence); the non-blank lines of its spec, and of each
    steps-section paragraph given to it under :data:`BOUND_HEADING`, in order; and its
    As filed block, from the LAST block opening with :data:`FILED_FROM` to the end.
    None when it is not a step card's shape."""
    head, opened, rest = body.partition(f"\n\n{SPEC_HEADING}\n\n")
    rest, filed, tail = rest.rpartition(f"\n\n{AS_FILED_OPENING}")
    if not (opened and filed):
        return None
    spec, *bound = rest.split(f"\n\n{BOUND_HEADING}\n\n")
    return (head, _filled(spec), [_filled(paragraph) for paragraph in bound],
            f"{AS_FILED_OPENING}{tail}")


def _filled(text: str) -> list[str]:
    """``text``'s non-blank lines: what the census counts."""
    return [line for line in text.splitlines() if line.strip()]


def census(inputs: Inputs,
           built: Mapping[str, Built]) -> tuple[dict[str, Counter], list[str]]:
    """Every non-blank line of each arc's steps section in exactly one place, by arc, and
    a refusal for each step card whose BUILT body, read back (:func:`step_regions`), is
    not what its sources make it: its sentence; its entry's non-blank lines as its spec;
    each given paragraph's non-blank lines, in the order the source puts them; its As
    filed block -- nothing missing, doubled, moved or added -- and for each line put on a
    card nobody built.  Blank lines and the dedent are not graded here (the module
    docstring)."""
    places, expected = {}, defaultdict(list)
    for arc, layout in inputs.source.layouts.items():
        places[arc] = Counter()
        for index, placed, card, block in _placed(arc, layout, inputs):
            places[arc][ON_A_CARD if card is not None else placed] += 1
            if card is not None:
                expected[card, (arc, block)].append((arc, index, placed))
    refusals = [f"census: line {lines[0][1] + 1} of {lines[0][0]}'s document belongs on "
                f"{card}'s card, which nothing builds" for (card, _), lines in expected.items()
                if card not in built]
    for item in inputs.source.items:
        if item.kind == "step" and item.key in built:
            refusals += _census_refusal(item, _wanted(item, inputs, expected),
                                        step_regions(built[item.key].body),
                                        as_filed(REGISTRY["step"], inputs.registries.commit,
                                                 _cells(item)))
    return places, refusals


def _wanted(item: Item, inputs: Inputs, expected: Mapping) -> list[list]:
    """What a step card must hold, block by block: its entry's lines, then each given
    paragraph's in the order :attr:`_migrate_source.Source.preambles` holds them (arc by
    arc, then line); each line ``(arc, index, text)``."""
    spec = inputs.source.specs.get(item.key)
    opener = None if spec is None else (item.arc, spec.entry.start)
    order = {(paragraph.arc, paragraph.start): position
             for position, paragraph in enumerate(inputs.source.preambles)}
    blocks = sorted((block for card, block in expected if card == item.key and block != opener),
                    key=order.__getitem__)
    return [expected.get((item.key, opener), []),
            *(expected[item.key, block] for block in blocks)]


def _census_refusal(item: Item, wanted: Sequence[Sequence[tuple[str, int, str]]],
                    regions: tuple[str, list[str], list[list[str]], str] | None,
                    block: str) -> list[str]:
    """The refusal for a step card whose body read back is not its sentence, ``wanted``
    (its spec's lines, then each given paragraph's) and its As filed ``block``, naming the
    first line out of place."""
    if regions is None:
        return [f"census: {item.key}'s card is not in a step card's shape"]
    head, spec, bound, tail = regions
    if (head, tail) != (item.row.title, block):
        return [f"census: {item.key}'s card holds text before its spec or after its As filed "
                "block's start that its sentence and its cells do not"]
    got = [spec, *bound]
    if [[text for _, _, text in lines] for lines in wanted] == got:
        return []
    if len(got) != len(wanted):
        return [f"census: {item.key}'s card holds {len(got) - 1} steps-section paragraphs "
                f"given to it; {len(wanted) - 1} are"]
    return [_first_misplaced(item, wanted, got)]


def _first_misplaced(item: Item, wanted: Sequence[Sequence[tuple[str, int, str]]],
                     got: Sequence[Sequence[str]]) -> str:
    """The first line of a step card out of its place, block by block, given that the
    card and its sources differ and hold as many blocks."""
    for lines, held in zip(wanted, got):
        for position, (arc, index, text) in enumerate(lines):
            if position >= len(held) or held[position] != text:
                return (f"census: line {index + 1} of {arc}'s document belongs on "
                        f"{item.key}'s card, and its built body does not hold it in its place")
        if len(held) > len(lines):
            return (f"census: {item.key}'s card holds a line no source line puts there: "
                    f"{held[len(lines)][:80]!r}")
    return f"census: {item.key}'s card differs from its sources in a way this census cannot name"


def _placed(arc: str, layout: Layout,
            inputs: Inputs) -> list[tuple[int, str, str | None, int | None]]:
    """Each non-blank line of ``arc``'s steps section: its index; either its place and no
    card, or the text its card must hold and that card's key; and the first line of the
    block it comes from (its entry, or its paragraph), within which a card holds its
    lines in order."""
    lines = inputs.registries.documents[arc].splitlines()
    entry_of = {index: entry for entry in layout.entries
                for index in range(entry.start, entry.stop)}
    preamble_of = {index: paragraph for paragraph in inputs.source.preambles
                   if paragraph.arc == arc for index in range(paragraph.start, paragraph.stop)}
    placed = []
    for index in range(*layout.span):
        if not lines[index].strip():
            continue
        if index in entry_of:
            placed.append((index, *_entry_place(arc, entry_of[index], index, lines, inputs)))
            continue
        paragraph = preamble_of.get(index)
        given = None if paragraph is None else inputs.decisions.preambles.get(
            (arc, paragraph.first_line))
        if given is not None and given.disposition == "card-body":
            placed.append((index, lines[index], given.fields["step"], paragraph.start))
        elif paragraph is not None:
            placed.append((index, "a steps-section paragraph kept for L8: "
                           + (given.disposition if given else "undecided"), None, None))
        else:
            placed.append((index, HEADING, None, None))
    return placed


def _entry_place(arc: str, entry: Entry, index: int, lines: Sequence[str],
                 inputs: Inputs) -> tuple[str, str | None, int | None]:
    """Where line ``index`` of ``entry`` goes: on its open step's card as :func:`dedent`
    makes it, unless the input file calls its paragraph a note; else it moves nowhere, as
    a note, the entry of a step already filed (:data:`_migrate_source.MAPPED`), a shipped
    step's pointer, or a checkbox for no step (refused by the source)."""
    key = f"{arc}:{entry.step}"
    spec = inputs.source.specs.get(key)
    if key in MAPPED:
        return FILED_ALREADY, None, None
    if spec is not None and index not in _note_lines(spec, inputs.decisions):
        return dedent(lines[index], spec.indent, index == entry.start), key, entry.start
    if spec is not None:
        return A_NOTE, None, None
    shipped = any(row.shipped for row in inputs.registries.steps
                  if (row.arc, row.bare_ident) == (arc, entry.step))
    return (SHIPPED if shipped else NO_STEP), None, None
