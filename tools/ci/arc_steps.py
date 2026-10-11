"""An arc document's steps: the checkbox grammar, the fence rule and each step's ENTRY, one home.

Two tools read an arc document's steps section.  The plan gate (``tools/plan_gate``)
grades it: rule 12 reconciles its checkboxes against ``steps.md``, and rule 7 grades
a shipped step's entry.  Step X-cx's migration (piece L7) lifts each open step's
entry into its card's body (ruling ``balance:R-BAL174``).  Both need the same three
facts: what a checkbox is, that a fenced code block holds samples rather than
records, and where a step's entry ends.  Until L7 the plan gate spelled the fence
rule twice itself (``_registry.arc_checkboxes`` re-implemented what
``_plan_gate._blank_fenced_regions`` stated), and a migration written beside them
would have been a third spelling of all three.  They live here, in the bottom layer
both tools import (ruling ``balance:R-BAL223``), and they leave with the steps
sections at X-cx's cutover (L8): every caller is a plan-gate arm or the migration.
One copy is left outside: ``tests/manual/verify_plan_registry_migration.py``, a
hand-run instrument of the registries' own migration, named for deletion at L8 by
the coordinator (2026-10-09) because its subject, the registries, goes then.

**An entry is a LINE SPAN, not text, and it indexes ``text.splitlines()``.**  The
plan gate slices the fence-blanked lines it has always graded; the migration slices
the ORIGINAL lines, so a code sample inside a specification moves intact.  Each
function here takes the document's TEXT and blanks it itself, line for line
(:func:`blank_fenced_lines`), so a caller can neither scan unblanked lines nor
slice a list of another length: joining the blanked lines and splitting them again
would drop a last line that a fence blanked, and a span would then lose the
closing fence of a section that ends the document.

Standard library only: CI runs this layer before any install
(``tools/ci/test_bottom_layer.py``).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

#: A steps-section checkbox: ``- [ ] **X-h** ...`` or the decomposed-leaf
#: spelling ``* [x] **X-g4a** ...``.  The bold run may carry more than the id
#: (``**X-i1 THE MEMO**``), so only the leading id token is captured.
CHECKBOX_RX = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<step>[A-Za-z0-9][A-Za-z0-9-]*)\b",
)


def fenced_lines(lines: Sequence[str]) -> list[bool]:
    """Return, for each of *lines*, whether it belongs to a fenced code block: the fence
    rule's one spelling.

    A line opening or closing a fence (```` ``` ````, however indented) belongs to it, and
    so does every line between, blank or not.  A fence that never closes runs to the end.
    X-cx's migration reads this as well as :func:`blank_fenced_lines`: a paragraph of a
    step's entry runs THROUGH a fenced sample's blank lines, which the blanked copy cannot
    tell from a blank line between two paragraphs.

    Args:
        lines: A document's lines (``text.splitlines()``).

    Returns:
        A list of the same length, True for each line of a fenced block.
    """
    out, inside = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append(True)
            continue
        out.append(inside)
    return out


def blank_fenced_lines(lines: Sequence[str]) -> list[str]:
    """Return *lines* with every fenced code block's lines blanked, one for one.

    The documents carry ``text`` fences, so a fence is not hypothetical.
    Blanking rather than deleting keeps every line at its index, and blanking
    rather than ignoring means a ``|`` row or a ``- [ ]`` line inside a code
    sample is not mistaken for a table row or a checkbox -- a code sample is an
    illustration, not a record.  A fence that never closes blanks to the end
    (:func:`fenced_lines`, the rule's one spelling).

    Args:
        lines: A document's lines (``text.splitlines()``).

    Returns:
        A list of the same length: each fence line and each line inside a fence
        empty, every other line unchanged.
    """
    return ["" if fenced else line for line, fenced in zip(lines, fenced_lines(lines))]


def blank_fenced_regions(text: str) -> str:
    """Return *text* with every fenced code block's contents blanked.

    :func:`blank_fenced_lines` joined back into one string, for the arms that
    scan a document as text rather than by line index.

    Args:
        text: The whole document.

    Returns:
        The document with fenced content replaced by empty lines.
    """
    return "\n".join(blank_fenced_lines(text.splitlines()))


@dataclass(frozen=True)
class Checkbox:
    """One checkbox: its line's index, its step id and whether it is ticked."""

    line: int
    step: str
    ticked: bool


@dataclass(frozen=True)
class Entry:
    """One step's entry: from its checkbox line (``start``) up to ``stop``, exclusive.

    The stop is the next checkbox, the next ``###`` line, or the end of the
    steps section, whichever comes first.
    """

    step: str
    ticked: bool
    start: int
    stop: int


class HeadingCountError(ValueError):
    """A document holds a section heading other than exactly once, so it has no one such
    section: ``found`` says how many lines the heading starts."""

    def __init__(self, heading: str, found: int) -> None:
        """Keep the heading and the count beside the message."""
        self.heading = heading
        self.found = found
        super().__init__(self.message("the document"))

    def message(self, where: str) -> str:
        """The refusal, naming the document as *where*: the one spelling of it."""
        return (f"expected exactly one heading starting {self.heading!r} in {where}; "
                f"found {self.found}")


def _boxes(lines: Sequence[str]) -> list[Checkbox]:
    """Every checkbox among already-blanked *lines*, indexed into them."""
    return [
        Checkbox(index, match.group("step"), match.group("tick").lower() == "x")
        for index, line in enumerate(lines)
        if (match := CHECKBOX_RX.match(line))
    ]


def _span(lines: Sequence[str], heading: str) -> tuple[int, int]:
    """The section headed *heading* among already-blanked *lines* (:func:`section_span`)."""
    starts = [index for index, line in enumerate(lines) if line.startswith(heading)]
    if len(starts) != 1:
        raise HeadingCountError(heading, len(starts))
    start = starts[0]
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return start, index
    return start, len(lines)


def checkboxes(text: str) -> list[Checkbox]:
    """Every checkbox in the document *text*, fenced samples excluded, in order.

    Args:
        text: The whole document.

    Returns:
        One :class:`Checkbox` per matching line, its ``line`` an index into
        ``text.splitlines()``.
    """
    return _boxes(blank_fenced_lines(text.splitlines()))


def section_span(text: str, heading: str) -> tuple[int, int]:
    """The ``##`` section whose heading line starts with *heading*: ``(start, stop)``.

    It runs from that heading line up to the next line starting ``## ``, or to
    the end of the document.  Fenced lines are blanked first, so a ``##`` line
    inside a code sample cannot end the section early.

    Args:
        text: The whole document.
        heading: The heading prefix (e.g. ``"## 5."``).

    Returns:
        The heading line's index and the stop index, exclusive, into
        ``text.splitlines()``.

    Raises:
        HeadingCountError: The heading starts no line, or more than one, so a
            restructured document is refused rather than read as an empty section.
    """
    return _span(blank_fenced_lines(text.splitlines()), heading)


def entries(text: str, heading: str) -> list[Entry]:
    """Every step's entry in the steps section headed *heading*, in document order.

    A step's ENTRY runs from its checkbox line to the next checkbox, the next
    ``###`` sub-heading, or the end of the steps section -- whichever comes
    first.  The sub-heading arm is load-bearing rather than defensive: the
    documents group steps under ``###`` headings (an umbrella over decomposed
    leaves, a block of carried steps), and without it the last step before such
    a heading absorbs the whole group's prose.

    Args:
        text: The whole document.
        heading: The steps section's heading prefix.

    Returns:
        One :class:`Entry` per checkbox in the section; each span indexes
        ``text.splitlines()`` and, line for line, its fence-blanked copy.

    Raises:
        HeadingCountError: Via :func:`section_span`.
    """
    start, stop = section_span(text, heading)
    lines = blank_fenced_lines(text.splitlines())
    marks = [Checkbox(start + box.line, box.step, box.ticked)
             for box in _boxes(lines[start:stop])]
    found = []
    for position, box in enumerate(marks):
        end = marks[position + 1].line if position + 1 < len(marks) else stop
        end = next((scan for scan in range(box.line + 1, end)
                    if lines[scan].startswith("###")), end)
        found.append(Entry(box.step, box.ticked, box.line, end))
    return found
