"""Every finding in the balance plan has a LIVE owner (Section 9 rule 6).

``docs/audits/balance_architecture/README.md`` is the balance arc's only live
planning document.  Its Section 6 ledger carries the findings that remain, and
its last column names who resolves each one.  Section 9 rule 6 fixes that
column's vocabulary and requires every value in it to be answerable:

* **a live (unticked) Section 5 step ID** -- the normal case;
* **``operator``** -- a question only the developer can answer from outside the
  code;
* **``developer-decision``** -- a fork the developer has taken, dated.

**Prose does not enforce itself, and the count is why this file exists.**  The
2026-07-27 triage found 29 of 41 open rows with no owner at all, and FOUR of
them naming a step that had already SHIPPED -- N-14, N-33, N-40, N-56, found by
reading the code rather than the document, weeks after the fact.  One commit
later X-o's trace found six more, every one naming a TICKED step: N-43, N-72,
N-78, N-85 and N-95 pointed at ``X-g4`` (annotated four different ways) and
N-46 at ``X-c2c4``.  That is three hand-passes in two days finding the same
class, and the document's own Section 8 says a safety that is a predicate is
not a safety -- this one was not even a predicate.

Reading a repository file by path and asserting on its text is an established
shape here: ``test_template_no_money_arithmetic.py`` and
``test_posting_ref_seed_parity.py`` both do it, and both run in CI on every PR
exactly as this does.

**The parser is strict on purpose.**  Every failure mode below was measured
against the ledger as it actually stands, because each of them would make a
naive implementation report a correct row as broken -- and a gate that cries
wolf is uninstalled, not fixed:

1. an owner cell is often an ANNOTATED id (``X-i1 (the redundancy)``,
   ``X-e (widened 2026-07-27; see also N-73)``), so the id is parsed OUT of the
   cell rather than the cell being read as an id;
2. a cell can name TWO owners for two halves of one row
   (``X-j (display) / X-e (cache)``), and both must be live -- and the split is
   taken at PAREN DEPTH ZERO, or an annotation that ever contains a slash
   (``X-b (display / cache)``) is torn into two bogus owners;
3. step IDs appear all over Section 5 as historical citations in prose -- the
   X-g tick line names X-g1 / X-g2 / X-g3, which are archived and deliberately
   not checkboxes -- so the "is this a checkbox" arm reads the OWNER COLUMN and
   nothing else;
4. the table escapes a literal pipe inside a cell as ``\\|`` (finding N-73's row
   carries ``Decimal \\| None``), so rows split on UNESCAPED pipes only.  A
   naive ``split("|")`` reads that row as six cells and reports the ledger
   broken when it is not;
5. Section 5 contains a FENCED code block (the target-shape diagram), so fenced
   regions are blanked before anything is parsed.  Otherwise a ``##``-prefixed
   line inside a fence truncates the section, every step after it vanishes, and
   the rows owning those steps fail accusing the LEDGER of naming a
   non-checkbox when it is the parser that lost the checkbox.

Rather than scan a cell for anything id-shaped -- which would try to validate
the ``N-73`` inside ``X-e (widened 2026-07-27; see also N-73)`` -- the cell must
MATCH the owner grammar.  Anything that does not is itself a failure, with the
cell quoted, which is what caught the one live violation when this was written.

**Three ways this gate could pass while seeing nothing are closed explicitly,
because a premise floor does not close them** -- each was found by the
adversarial review of this file and each is demonstrated by its own control
below: a row whose id cell is EMPTY (``set("") <= {"-", ":"}`` is ``True``, so
a naive delimiter test drops it and never reads its owner); a DUPLICATE
checkbox id, where the last occurrence wins and can silently un-tick a shipped
step; and the fenced-region truncation above.
"""

from __future__ import annotations

import re
from pathlib import Path

PLAN_PATH = Path("docs/audits/balance_architecture/README.md")

#: Headings that bound the two sections this gate reads.  Matched on the
#: numbered prefix, which is stable: the document renumbers nothing.
_STEPS_HEADING = "## 5."
_LEDGER_HEADING = "## 6."

#: A Section 5 checkbox: ``- [ ] **X-h** ...`` or the decomposed-leaf spelling
#: ``* [x] **X-g4a** ...``.  The bold run may carry more than the id
#: (``**X-i1 THE MEMO**``), so only the leading id token is captured.
_CHECKBOX_RX = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<step>[A-Za-z0-9][A-Za-z0-9-]*)\b",
)

#: Split a markdown table row on pipes that are not backslash-escaped.
_UNESCAPED_PIPE_RX = re.compile(r"(?<!\\)\|")

#: One owner: an id or vocabulary word, optionally annotated in parentheses.
#: The annotation may itself contain a parenthesised aside, which the inner
#: alternation allows without turning the whole thing into a wildcard.
_OWNER_RX = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)"
    r"(?: \((?P<note>[^()]*(?:\([^()]*\)[^()]*)*)\))?$",
)

#: The non-step values Section 9 rule 6 admits.  There is deliberately no value
#: meaning "someone will get to it".  Each carries a REQUIREMENT the rule states
#: and the gate therefore checks: ``operator`` must state the question,
#: ``developer-decision`` must be dated.
_NON_STEP_OWNERS = frozenset({"operator", "developer-decision"})

#: An ISO date inside an annotation, which ``developer-decision`` must carry.
_DATED_RX = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Number of columns in the Section 6 table.
_LEDGER_COLUMNS = 5


def _blank_fenced_regions(text: str) -> str:
    """Return *text* with every fenced code block's contents blanked.

    Section 5 carries a ``text`` fence (the target-shape diagram), so a fence is
    not hypothetical here.  Blanking rather than deleting keeps line positions
    intact, and blanking rather than ignoring means a ``|`` row or a ``- [ ]``
    line inside a code sample is not mistaken for a table row or a checkbox --
    a code sample is an illustration, not a record.

    Args:
        text: The whole document.

    Returns:
        The document with fenced content replaced by empty lines.
    """
    out, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _split_owners(cell: str) -> list[str]:
    """Split an owner cell on ``" / "`` at parenthesis depth zero.

    A plain ``cell.split(" / ")`` tears ``X-b (display / cache)`` into
    ``X-b (display`` and ``cache)``, and reports TWO grammar violations on a
    cell that is fine.  No live cell has a slash inside an annotation today, but
    the live annotations are prose -- ``X-e (widened 2026-07-27; see also
    N-73)`` -- so one is an ordinary next edit, and this gate's own thesis is
    that a gate which cries wolf gets uninstalled rather than fixed.

    Args:
        cell: The owner cell's text.

    Returns:
        The owner substrings, in order.
    """
    parts, depth, start, index = [], 0, 0, 0
    while index < len(cell):
        char = cell[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and cell.startswith(" / ", index):
            parts.append(cell[start:index])
            index += len(" / ")
            start = index
            continue
        index += 1
    parts.append(cell[start:])
    return parts


def _section(text: str, heading: str) -> str:
    """Return the body of the ``##`` section whose heading starts with *heading*.

    Fenced regions are blanked first (:func:`_blank_fenced_regions`), so a
    ``##``-prefixed line inside a code sample cannot end the section early.

    Args:
        text: The whole document.
        heading: The heading prefix to locate (e.g. ``"## 5."``).

    Returns:
        Everything from that heading up to the next ``##`` heading.

    Raises:
        AssertionError: The heading is absent, so a restructured document fails
            loudly here rather than silently reporting an empty section.
    """
    lines = _blank_fenced_regions(text).splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
    assert len(starts) == 1, (
        f"expected exactly one heading starting {heading!r} in {PLAN_PATH}; "
        f"found {len(starts)}"
    )
    start = starts[0]
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def parse_steps(text: str) -> dict[str, bool]:
    """Return ``{step id: is ticked}`` for every Section 5 checkbox.

    Args:
        text: The whole document.

    Returns:
        Each checkbox step id mapped to whether its box is ticked.

    Raises:
        AssertionError: A step id appears on more than one checkbox.  The last
            one would win, so a step re-listed as unticked after it shipped
            would silently un-tick itself and every row owning it would pass --
            blinding the arm this gate exists for.  This document re-parents
            and re-lists steps routinely (``X-c2c4`` was re-parented to the top
            level), so the collision is a live edit away.
    """
    steps = {}
    for line in _section(text, _STEPS_HEADING).splitlines():
        match = _CHECKBOX_RX.match(line)
        if match is None:
            continue
        step = match.group("step")
        assert step not in steps, (
            f"step {step!r} has more than one checkbox in Section 5; the last "
            "would win and could silently un-tick a shipped step"
        )
        steps[step] = match.group("tick").lower() == "x"
    return steps


def parse_ledger(text: str) -> list[tuple[str, str]]:
    """Return ``(finding id, owner cell)`` for every Section 6 data row.

    The header and its delimiter row are skipped; every other table row is a
    finding.

    Args:
        text: The whole document.

    Returns:
        One pair per finding row, in document order.

    Raises:
        AssertionError: A row does not have the table's column count, which
            means an unescaped ``|`` inside a cell has split it -- the row
            renders wrong, and reading its last cell as the owner would be
            reading the wrong cell.
    """
    rows = []
    for line in _section(text, _LEDGER_HEADING).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in _UNESCAPED_PIPE_RX.split(line.strip())]
        # A leading and trailing pipe produce empty edge cells.
        cells = cells[1:-1]
        # ``set(cells[0])`` must be non-empty to be a delimiter: an EMPTY id
        # cell is a subset of every set, so a bare membership test drops the
        # row and never looks at its owner -- silent, and exactly the vacuity
        # the premise floors cannot see.
        if not cells or (set(cells[0]) and set(cells[0]) <= {"-", ":"}):
            continue  # the delimiter row
        if cells[0] == "id":
            continue  # the header
        assert len(cells) == _LEDGER_COLUMNS, (
            f"ledger row {cells[0]!r} split into {len(cells)} cells, not "
            f"{_LEDGER_COLUMNS} -- an unescaped '|' inside a cell splits the "
            r"row; write it as '\|'"
        )
        rows.append((cells[0], cells[-1]))
    return rows


def _vocabulary_violations(
    finding: str, owner: str, note: str | None,
) -> list[str]:
    """Return the violations for a non-step owner's stated requirement.

    Rule 6 does not admit ``operator`` and ``developer-decision`` as bare
    words: an operator row states THE QUESTION, and a developer-decision is
    DATED with the options named.  Both halves are what make the value an
    answer rather than a shrug, and the dated half is mechanically checkable.

    Args:
        finding: The finding id, for the message.
        owner: The vocabulary word.
        note: Its parenthesised annotation, or ``None``.

    Returns:
        Zero or one violation message.
    """
    if not note:
        return [
            f"{finding}: owner {owner!r} carries no annotation -- rule 6 "
            "requires the question stated (operator) or the fork named and "
            "dated (developer-decision), not the bare word"
        ]
    if owner == "developer-decision" and not _DATED_RX.search(note):
        return [
            f"{finding}: owner 'developer-decision' is not DATED -- rule 6 "
            f"requires the date the fork was taken; got {note!r}"
        ]
    return []


def owner_violations(text: str) -> list[str]:
    """Return one message per Section 9 rule 6 violation in the ledger.

    Args:
        text: The whole document.

    Returns:
        A list of human-readable violations; empty when every owner is live.
    """
    steps = parse_steps(text)
    violations = []
    for finding, cell in parse_ledger(text):
        if not cell:
            violations.append(f"{finding}: no owner at all (rule 6)")
            continue
        for part in _split_owners(cell):
            match = _OWNER_RX.match(part)
            if match is None:
                violations.append(
                    f"{finding}: owner {part!r} is not an owner -- rule 6's "
                    "column is a ' / '-separated list of ids or vocabulary "
                    "words, each optionally annotated in parentheses"
                )
                continue
            owner, note = match.group("owner"), match.group("note")
            if owner in _NON_STEP_OWNERS:
                violations.extend(_vocabulary_violations(finding, owner, note))
                continue
            if owner not in steps:
                violations.append(
                    f"{finding}: owner {owner!r} is not a Section 5 checkbox "
                    "-- an owner must be TICKABLE, or it is outside rule 6's "
                    f"vocabulary {sorted(_NON_STEP_OWNERS)}"
                )
                continue
            if steps[owner]:
                violations.append(
                    f"{finding}: owner {owner!r} has SHIPPED (its box is "
                    "ticked).  Rule 2: a step that ships re-points every row "
                    "that named it"
                )
    return violations


class TestTheBalancePlanLedgerHasNoUnownedRows:
    """The gate rule 6 calls for: this document's owners are all answerable."""

    def test_the_parser_finds_the_document_it_is_grading(self):
        """Premise: the steps and the ledger are actually being read.

        Asserted first and separately because every check below passes
        vacuously against an empty parse -- a regex that matched nothing would
        report a perfect ledger.  The floors are far under the live counts (27
        checkboxes and 38 ledger ROWS when this was written -- the table also
        carries a header and a delimiter line, so a naive count of lines
        starting with ``|`` gives 40 and is not what is floored here) so
        ordinary growth and ordinary archiving do not touch them, while a
        parser that silently stops working does.

        A floor is not enough on its own, which is why the three silent-parse
        holes the adversarial review found are closed at their source rather
        than covered here: an empty id cell, a duplicate checkbox id, and a
        ``##`` line inside a fence would each keep the counts plausible.
        """
        text = PLAN_PATH.read_text(encoding="utf-8")
        steps = parse_steps(text)
        rows = parse_ledger(text)
        assert len(steps) >= 15, (
            f"parsed only {len(steps)} Section 5 checkboxes: {sorted(steps)}"
        )
        assert any(not ticked for ticked in steps.values()), (
            "parsed no LIVE step at all -- every owner would fail"
        )
        assert any(ticked for ticked in steps.values()), (
            "parsed no SHIPPED step at all -- the stale-owner arm could never "
            "fire, which is the arm this gate exists for"
        )
        assert len(rows) >= 25, f"parsed only {len(rows)} ledger rows"

    def test_every_finding_has_a_live_owner(self):
        """No row names a shipped step, an unknown id, or a retired word."""
        violations = owner_violations(PLAN_PATH.read_text(encoding="utf-8"))
        assert violations == [], (
            "docs/audits/balance_architecture/README.md Section 6 violates "
            "Section 9 rule 6:\n  " + "\n  ".join(violations)
        )


class TestTheGateItselfFires:
    """The negative controls: each arm is shown to bite on a planted defect.

    Section 7.3 of the plan: a guard whose control does not fire is not a
    guard.  Each case below plants ONE defect in a synthetic document that is
    otherwise valid, and asserts the arm names it -- so a future edit that
    loosens the parser cannot pass silently.
    """

    _DOC = """\
## 5. The steps

- [x] **X-a** the shipped one
- [ ] **X-b** the live one
* [ ] **X-b1 THE LEAF** a decomposed leaf, live

## 6. The findings ledger

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| N-1 | a thing | -- | OPEN | X-b |
| N-2 | a thing with a `Decimal \\| None` pipe | -- | OPEN | X-b1 (annotated) |
| N-3 | two halves | -- | OPEN | X-b (display) / X-b1 (cache) |
| FU-1 | an operator question | -- | OPEN | operator (unchanged) |
| N-4 | a taken fork | -- | OPEN | developer-decision (dated 2026-07-27) |

## 7. Verification standard
"""

    def test_the_clean_document_passes(self):
        """Premise: the synthetic document is valid, so each defect is the only one."""
        assert owner_violations(self._DOC) == []

    def test_an_owner_that_has_shipped_is_caught(self):
        """The exact class that went unnoticed for weeks, three times."""
        broken = self._DOC.replace("| OPEN | X-b |", "| OPEN | X-a |")
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "N-1" in violations[0] and "SHIPPED" in violations[0]

    def test_an_owner_naming_no_step_is_caught(self):
        """An id that is not a checkbox cannot answer "did its owner ship?"."""
        broken = self._DOC.replace("| OPEN | X-b |", "| OPEN | X-zz |")
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "'X-zz'" in violations[0] and "TICKABLE" in violations[0]

    def test_a_retired_vocabulary_word_is_caught(self):
        """"Own commit" and its siblings all mean nobody (rule 6)."""
        broken = self._DOC.replace("| OPEN | X-b |", "| OPEN | own commit |")
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "not an owner" in violations[0]

    def test_one_bad_half_of_a_two_owner_cell_is_caught(self):
        """Both halves of ``A / B`` must be live, not just the first."""
        broken = self._DOC.replace(
            "X-b (display) / X-b1 (cache)", "X-b (display) / X-a (cache)",
        )
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "N-3" in violations[0] and "SHIPPED" in violations[0]

    def test_an_empty_owner_cell_is_caught(self):
        """A row with no owner is unfinished work, not a recorded finding."""
        broken = self._DOC.replace("| OPEN | X-b |", "| OPEN |  |")
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "no owner at all" in violations[0]

    def test_an_unescaped_pipe_is_reported_as_a_split_row(self):
        """A broken row fails as a broken ROW, not as a mystery owner.

        The distinction is the whole reason the parser splits on unescaped
        pipes: the real ledger carries ``Decimal \\| None`` inside a cell, and a
        parser that could not see the escape would report that correct row as
        having the wrong owner.
        """
        broken = self._DOC.replace(r"`Decimal \| None`", "`Decimal | None`")
        try:
            owner_violations(broken)
        except AssertionError as exc:
            assert "6 cells" in str(exc) and "N-2" in str(exc), str(exc)
        else:
            raise AssertionError("an unescaped pipe was not reported")

    def test_a_row_with_an_empty_id_cell_is_still_graded(self):
        """An empty id cell must not be mistaken for the delimiter row.

        ``set("") <= {"-", ":"}`` is ``True``, so a bare subset test drops the
        row and never reads its owner.  The premise floors cannot see it: the
        live table could lose 13 rows and still clear ``>= 25``.
        """
        broken = self._DOC.replace("| N-1 | a thing |", "|  | a thing |")
        broken = broken.replace("| OPEN | X-b |", "| OPEN | X-a |")
        violations = owner_violations(broken)
        assert len(violations) == 1, violations
        assert "SHIPPED" in violations[0], violations

    def test_a_duplicate_checkbox_id_is_refused(self):
        """A step re-listed in Section 5 would silently un-tick itself.

        The last occurrence wins, so re-listing a SHIPPED step as unticked
        blinds the arm this gate exists for.  It must fail as a duplicate, not
        pass as a live owner.
        """
        broken = self._DOC.replace(
            "* [ ] **X-b1 THE LEAF** a decomposed leaf, live",
            "* [ ] **X-b1 THE LEAF** a decomposed leaf, live\n"
            "- [ ] **X-a** re-listed in a later summary",
        )
        try:
            owner_violations(broken)
        except AssertionError as exc:
            assert "'X-a'" in str(exc) and "more than one checkbox" in str(exc)
        else:
            raise AssertionError("a duplicate checkbox id was not reported")

    def test_a_fence_cannot_truncate_the_steps_section(self):
        """A ``##`` line inside a code sample must not end Section 5.

        Section 5 really does carry a fenced diagram.  Without fence-blanking
        every step after the fence vanishes, and the rows owning them fail
        accusing the LEDGER of naming a non-checkbox -- a true failure with a
        false diagnosis, which is worse than no gate.
        """
        broken = self._DOC.replace(
            "* [ ] **X-b1 THE LEAF** a decomposed leaf, live",
            "* [ ] **X-b1 THE LEAF** a decomposed leaf, live\n\n"
            "```text\n## sample heading inside a fence\n```",
        )
        assert owner_violations(broken) == []

    def test_a_bare_vocabulary_word_is_refused(self):
        """``operator`` states its question and ``developer-decision`` is dated."""
        bare = self._DOC.replace("operator (unchanged)", "operator")
        violations = owner_violations(bare)
        assert len(violations) == 1, violations
        assert "carries no annotation" in violations[0]

        undated = self._DOC.replace(
            "developer-decision (dated 2026-07-27)",
            "developer-decision (the fork)",
        )
        violations = owner_violations(undated)
        assert len(violations) == 1, violations
        assert "is not DATED" in violations[0]

    def test_a_slash_inside_an_annotation_is_not_a_second_owner(self):
        """``X-b (display / cache)`` is ONE annotated owner, not two broken ones.

        The split is taken at parenthesis depth zero.  A plain
        ``cell.split(" / ")`` reports two grammar violations on a cell that is
        fine -- a gate that cries wolf gets uninstalled, not fixed.
        """
        annotated = self._DOC.replace("| OPEN | X-b |", "| OPEN | X-b (display / cache) |")
        assert owner_violations(annotated) == []
