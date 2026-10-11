"""``tools/ci/arc_steps``: the checkbox grammar, the fence rule and a step's entry.

Synthetic documents only.  The live arc documents are graded by the plan gate's
suite (``tools/plan_gate/test_arc_documents.py``), whose pre-commit hook runs on
every edit of an arc document; this package's hook runs on edits under
``tools/ci`` and the workflows only.
"""
from __future__ import annotations

import pytest

from tools.ci.arc_steps import (
    CHECKBOX_RX,
    Entry,
    HeadingCountError,
    blank_fenced_lines,
    blank_fenced_regions,
    checkboxes,
    entries,
    fenced_lines,
    section_span,
)

#: A document with a signpost before the steps, a fenced sample, a ``###`` group
#: and a section after the steps.
DOC = """# Title

## 1. Where it stands

- [ ] **Z-0** a checkbox OUTSIDE the steps section

## 4. Steps

Preamble of the section.

- [x] **A-1 -- shipped.** `abc1234` -- a pointer.
- [ ] **A-2** an open step
  its continuation
  ```text
  - [ ] **A-99** a sample, not a step
## a heading in a sample, at column 0
  ```
- [ ] **A-3** another open step

### A group of carried steps

prose of the group

* [ ] **A-4 THE MEMO** a starred leaf

## 5. After the steps

- [ ] **Z-1** after
"""


def _index(text: str, needle: str) -> int:
    """The index of the first line of *text* holding *needle*."""
    return next(i for i, line in enumerate(text.splitlines()) if needle in line)


class TestTheGrammar:
    """What a checkbox is."""

    @pytest.mark.parametrize("line, step, tick", [
        ("- [ ] **X-h** text", "X-h", " "),
        ("* [x] **X-g4a** text", "X-g4a", "x"),
        ("  - [X] **X-i1 THE MEMO** text", "X-i1", "X"),
    ])
    def test_both_spellings_and_an_annotated_bold_run(self, line, step, tick):
        """The leading id token alone is the step; the tick keeps its case."""
        match = CHECKBOX_RX.match(line)
        assert match is not None
        assert (match.group("step"), match.group("tick")) == (step, tick)

    @pytest.mark.parametrize("line", [
        "- [ ] X-h not bold", "- **X-h** no box", "text - [ ] **X-h**",
    ])
    def test_a_line_that_is_not_a_checkbox(self, line):
        """No box, no bold run, or not at the line's start: no checkbox."""
        assert CHECKBOX_RX.match(line) is None

    @pytest.mark.parametrize("box, ticked", [("[x]", True), ("[X]", True), ("[ ]", False)])
    def test_an_upper_case_tick_is_ticked(self, box, ticked):
        """``[X]`` is as shipped as ``[x]``: the tick is read case-blind."""
        assert checkboxes(f"* {box} **A-5** a leaf")[0].ticked is ticked


class TestTheFenceRule:
    """A code sample is an illustration, not a record."""

    def test_fenced_lines_are_blanked_one_for_one(self):
        """Every line keeps its index; the fence lines and their contents are empty."""
        original = DOC.splitlines()
        blanked = blank_fenced_lines(original)
        assert len(blanked) == len(original)
        start, stop = _index(DOC, "```text"), _index(DOC, "## a heading in a sample") + 1
        assert all(line == "" for line in blanked[start:stop + 1])
        assert blanked[start - 1] == original[start - 1]
        assert blank_fenced_regions(DOC) == "\n".join(blanked)

    def test_a_fence_owns_its_blank_lines_and_an_open_one_runs_to_the_end(self):
        """:func:`fenced_lines`, the rule's one spelling: the fence lines and every line
        between, BLANK ones included, belong to the block, which blanking cannot show; an
        indented fence counts; a fence never closed runs to the end."""
        lines = ["text", "  ```text", "a", "", "b", "  ```", "", "after", "```", "", "open"]
        assert fenced_lines(lines) == [False, True, True, True, True, True, False, False,
                                       True, True, True]
        assert blank_fenced_lines(lines) == [
            "text", "", "", "", "", "", "", "after", "", "", ""]

    @pytest.mark.parametrize("text", [
        "- [ ] **B-1** spec\n```text\nsample\n```\n",
        "- [ ] **B-1** spec\n```text\nsample\n",
        "- [ ] **B-1** spec\r\n```text\r\nsample\r\n```\r\n",
    ])
    def test_a_fence_that_ends_the_document_keeps_its_lines(self, text):
        """A closing fence, or fenced content, as the LAST line is blanked, not dropped:
        joining blanked lines and splitting them again would lose it."""
        assert len(blank_fenced_lines(text.splitlines())) == len(text.splitlines())

    def test_a_checkbox_inside_a_fence_is_not_one(self):
        """The sample's ``A-99`` is not found; every real checkbox is, at its own index."""
        found = checkboxes(DOC)
        assert [box.step for box in found] == ["Z-0", "A-1", "A-2", "A-3", "A-4", "Z-1"]
        assert [box.ticked for box in found] == [False, True, False, False, False, False]
        assert found[1].line == _index(DOC, "**A-1")

    def test_an_unclosed_fence_blanks_to_the_end(self):
        """A fence that never closes swallows the rest of the document."""
        text = "- [ ] **B-1** before\n```\n- [ ] **B-2** inside\n"
        assert [box.step for box in checkboxes(text)] == ["B-1"]


class TestTheSection:
    """Where the steps section is."""

    def test_it_runs_to_the_next_level_two_heading(self):
        """A ``###`` and a fenced ``##`` do not end it; ``## 5.`` does."""
        assert section_span(DOC, "## 4.") == (_index(DOC, "## 4. Steps"),
                                              _index(DOC, "## 5. After"))

    def test_a_fence_that_ends_the_document_is_inside_the_last_section(self):
        """A closing fence as the document's last line is still the section's: its stop is
        the document's length, not one short of it."""
        text = "## 4. Steps\n\n- [ ] **B-1** spec\n```text\nsample\n```\n"
        assert section_span(text, "## 4.") == (0, len(text.splitlines()))

    def test_the_last_section_runs_to_the_end(self):
        """No later ``##`` heading: the section is the rest of the document."""
        assert section_span(DOC, "## 5.") == (_index(DOC, "## 5. After"),
                                              len(DOC.splitlines()))

    @pytest.mark.parametrize("text, found", [
        (DOC.replace("## 4. Steps", "## Renamed"), 0),
        (DOC + "\n## 4. Again\n", 2),
    ])
    def test_an_absent_or_doubled_heading_is_refused(self, text, found):
        """A restructured document is refused, never read as an empty section."""
        with pytest.raises(HeadingCountError) as refused:
            section_span(text, "## 4.")
        assert (refused.value.heading, refused.value.found) == ("## 4.", found)
        assert "## 4." in str(refused.value)
        assert refused.value.message("doc.md") == (
            f"expected exactly one heading starting '## 4.' in doc.md; found {found}"
        )


class TestTheEntries:
    """Where a step's entry ends."""

    def test_each_entry_stops_at_the_next_checkbox_a_group_heading_or_the_section_end(self):
        """Four entries, in order, none outside the section."""
        assert entries(DOC, "## 4.") == [
            Entry("A-1", True, _index(DOC, "**A-1"), _index(DOC, "**A-2")),
            Entry("A-2", False, _index(DOC, "**A-2"), _index(DOC, "**A-3")),
            Entry("A-3", False, _index(DOC, "**A-3"), _index(DOC, "### A group")),
            Entry("A-4", False, _index(DOC, "**A-4"), _index(DOC, "## 5. After")),
        ]

    def test_a_span_slices_the_original_text_with_its_sample_intact(self):
        """The migration's half: the same span over the ORIGINAL lines keeps the fenced
        sample, which the blanked lines the gate grades do not."""
        entry = entries(DOC, "## 4.")[1]
        original = DOC.splitlines()[entry.start:entry.stop]
        blanked = blank_fenced_lines(DOC.splitlines())[entry.start:entry.stop]
        assert "  - [ ] **A-99** a sample, not a step" in original
        assert not any("A-99" in line for line in blanked)
        assert original[0] == "- [ ] **A-2** an open step"

    def test_the_last_entry_of_a_section_ending_the_document_keeps_its_closing_fence(self):
        """A steps section that runs to the end of the document, whose last line closes a
        fence: the span still reaches it, so the migration moves the sample whole."""
        text = "## 4. Steps\n\n- [ ] **B-1** spec\n  ```text\n  sample\n  ```\n"
        entry = entries(text, "## 4.")[0]
        assert text.splitlines()[entry.start:entry.stop][-1] == "  ```"

    def test_a_section_with_no_checkbox_has_no_entries(self):
        """A heading over prose alone: an empty list, not a refusal."""
        assert not entries("## 4. Steps\n\nprose only\n\n## 5. Next\n", "## 4.")
