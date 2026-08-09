"""Document-agnostic machinery for grading a planning document's ledger.

Two planning documents in this repository carry a findings ledger whose last
column must name a LIVE owner, and both are graded the same way:

* ``docs/audits/balance_architecture/README.md`` (the balance arc), the
  document this machinery was written for and measured against;
* ``docs/plans/implementation_plan_recurrence_redesign.md`` (the recurrence
  redesign), which adopted the same rules rather than inventing a second
  shape.

**The parser lives here and the two documents' specs live in their own gate
files**, because the machinery is where the measured false-positive fixes are
and duplicating it would mean maintaining those fixes twice.  Each fix below
was found against a REAL ledger, and each would make a naive implementation
report a correct row as broken -- a gate that cries wolf is uninstalled, not
fixed:

1. an owner cell is often an ANNOTATED id (``X-i1 (the redundancy)``,
   ``X-e (widened 2026-07-27; see also N-73)``), so the id is parsed OUT of the
   cell rather than the cell being read as an id;
2. a cell can name TWO owners for two halves of one row
   (``X-j (display) / X-e (cache)``), and both must be live -- and the split is
   taken at PAREN DEPTH ZERO, or an annotation that ever contains a slash
   (``X-b (display / cache)``) is torn into two bogus owners;
3. step IDs appear all over a steps section as historical citations in prose,
   so the "is this a checkbox" arm reads the OWNER COLUMN and nothing else;
4. a table escapes a literal pipe inside a cell as ``\\|`` (the balance
   ledger's N-73 row carries ``Decimal \\| None``), so rows split on UNESCAPED
   pipes only.  A naive ``split("|")`` reads that row as six cells and reports
   the ledger broken when it is not;
5. a steps section can contain a FENCED code block, so fenced regions are
   blanked before anything is parsed.  Otherwise a ``##``-prefixed line inside
   a fence truncates the section, every step after it vanishes, and the rows
   owning those steps fail accusing the LEDGER of naming a non-checkbox when it
   is the parser that lost the checkbox.

Rather than scan a cell for anything id-shaped -- which would try to validate
the ``N-73`` inside ``X-e (widened 2026-07-27; see also N-73)`` -- the cell must
MATCH the owner grammar.  Anything that does not is itself a failure, with the
cell quoted.

**Three ways a gate built on this could pass while seeing nothing are closed
explicitly, because a premise floor does not close them**: a row whose id cell
is EMPTY (``set("") <= {"-", ":"}`` is ``True``, so a naive delimiter test drops
it and never reads its owner); a DUPLICATE checkbox id, where the last
occurrence wins and can silently un-tick a shipped step; and the fenced-region
truncation above.  Each has a negative control in each gate file.

Every message this module emits is composed from the caller's :class:`PlanSpec`
labels, so a violation names the section and rule of the document it was
actually found in.  A shared parser that told a recurrence reader to consult
"Section 9 rule 6" would be a worse gate than no gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: A steps-section checkbox: ``- [ ] **X-h** ...`` or the decomposed-leaf
#: spelling ``* [x] **X-g4a** ...``.  The bold run may carry more than the id
#: (``**X-i1 THE MEMO**``), so only the leading id token is captured.
_CHECKBOX_RX = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<step>[A-Za-z0-9][A-Za-z0-9-]*)\b",
)

#: Split a markdown table row on pipes that are not backslash-escaped.
_UNESCAPED_PIPE_RX = re.compile(r"(?<!\\)\|")

#: One owner: an id or vocabulary word, optionally annotated in parentheses.
#: The annotation may itself contain a parenthesised aside, which the inner
#: alternation allows without turning the whole thing into a wildcard.
OWNER_RX = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)"
    r"(?: \((?P<note>[^()]*(?:\([^()]*\)[^()]*)*)\))?$",
)

#: The non-step owner values both documents admit.  There is deliberately no
#: value meaning "someone will get to it".  Each carries a REQUIREMENT the
#: owning rule states and this module therefore checks: ``operator`` must state
#: the question, ``developer-decision`` must be dated.
NON_STEP_OWNERS = frozenset({"operator", "developer-decision"})

#: An ISO date inside an annotation, which ``developer-decision`` must carry.
_DATED_RX = re.compile(r"\d{4}-\d{2}-\d{2}")

#: The sentence a ledger states its own size with.  Shared rather than
#: re-declared per document: the balance gate's first draft required
#: ``rows**`` and so matched the live file NOWHERE -- it read as "no count
#: claimed", and a planted 38-against-41 passed.  One definition means that
#: blindness cannot be reintroduced in one document and not the other.  The
#: full stop may sit INSIDE the emphasis, which is how both documents write it.
STATED_COUNT_RX = re.compile(
    r"\*\*The ledger stands at (?P<count>\d+) rows?\.?\*\*",
)

#: A ticked step's opening line, split into its bold title and what follows.
#: The title runs non-greedily to its own closing ``**`` so a title containing
#: backticked code (``**R6 -- Delete `payment_day`.**``) is not mistaken for
#: the citation.
_TICKED_OPENER_RX = re.compile(
    r"^\s*[-*]\s*\[[xX]\]\s*\*\*(?P<title>.*?)\*\*\s*(?P<rest>.*)$",
)

#: The commit citation, required to be the FIRST thing after the title.
#: Position is the discriminator, not shape: an Alembic revision id is also 12
#: hex characters and would satisfy any "contains a hash" test while naming no
#: commit -- both live documents cite one in the same breath as their commit.
_COMMIT_CITATION_RX = re.compile(r"^`([0-9a-f]{7,40})`")


@dataclass(frozen=True)
class PlanSpec:
    """One planning document's identity, structure, caps and vocabulary.

    A parameter object rather than a bag of arguments: every field is read by
    more than one violation function, and threading them individually made the
    call sites unreadable.  The message-label fields exist so a violation cites
    the section and rule of the document it was found in -- the two documents
    number their sections differently, and a message pointing at the wrong one
    sends the reader to a section that does not discuss the rule they broke.

    Attributes:
        path: The document, resolved absolutely so the gate grades the same
            file however pytest is invoked.
        steps_heading: Heading prefix bounding the steps section
            (e.g. ``"## 5."``).  Matched on the numbered prefix, which is
            stable: neither document renumbers.
        steps_label: How to name that section in a message (e.g. ``"Section
            5"``).
        ledger_heading: Heading prefix bounding the findings ledger.
        ledger_label: How to name the ledger section in a message.
        ledger_columns: The ledger table's column count.  A row that splits
            into a different number has an unescaped pipe in a cell, which
            means the last cell is not the owner.
        owner_rule: The document's own citation for the live-owner rule
            (e.g. ``"Section 9 rule 6"``).
        ship_rule: Its citation for "a step that ships re-points every row that
            named it".
        line_cap: The whole-document line cap.
        line_cap_rule: Its citation for that cap.
        archive_rule: Its citation for the archive-a-completed-span remedy --
            the ONLY legal way to come back under the cap.
        stated_count_rx: Matches the ledger's "stands at N rows" sentence, with
            a ``count`` group.  The sentence is optional in the document; this
            pattern is not, because an absent pattern would make the arm
            vacuous rather than lenient.
        arc_state_heading: Heading of the short orientation section.
        arc_state_cap: Its line cap.
        rules_label: Where this document states its standing rules
            (e.g. ``"Sections 7-9"``), named in the relocation advice so the
            reader is sent to a section that exists.
        ticked_entry_cap: Line cap on a TICKED step's entry, or ``None`` to
            leave the arm off.  A shipped step is a POINTER: a sentence of
            what it did and its commit hash.  ``None`` is not "no opinion" --
            it means this document has not adopted the rule yet, and the spec
            says why.
        non_step_owners: The vocabulary words admitted beside step ids.
    """

    path: Path
    steps_heading: str
    steps_label: str
    ledger_heading: str
    ledger_label: str
    ledger_columns: int
    owner_rule: str
    ship_rule: str
    line_cap: int
    line_cap_rule: str
    archive_rule: str
    stated_count_rx: re.Pattern[str]
    arc_state_heading: str
    arc_state_cap: int
    rules_label: str
    ticked_entry_cap: int | None = None
    non_step_owners: frozenset[str] = NON_STEP_OWNERS

    def read(self) -> str:
        """Return the document's full text.

        Returns:
            The file's contents, decoded as UTF-8.
        """
        return self.path.read_text(encoding="utf-8")


def _blank_fenced_regions(text: str) -> str:
    """Return *text* with every fenced code block's contents blanked.

    Both documents carry ``text`` fences inside their steps sections, so a
    fence is not hypothetical.  Blanking rather than deleting keeps line
    positions intact, and blanking rather than ignoring means a ``|`` row or a
    ``- [ ]`` line inside a code sample is not mistaken for a table row or a
    checkbox -- a code sample is an illustration, not a record.

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


def split_owners(cell: str) -> list[str]:
    """Split an owner cell on ``" / "`` at parenthesis depth zero.

    A plain ``cell.split(" / ")`` tears ``X-b (display / cache)`` into
    ``X-b (display`` and ``cache)``, and reports TWO grammar violations on a
    cell that is fine.  The live annotations are prose -- ``X-e (widened
    2026-07-27; see also N-73)`` -- so a slash inside one is an ordinary next
    edit.

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


def section(text: str, heading: str, *, label: str) -> str:
    """Return the body of the ``##`` section whose heading starts with *heading*.

    Fenced regions are blanked first (:func:`_blank_fenced_regions`), so a
    ``##``-prefixed line inside a code sample cannot end the section early.

    Args:
        text: The whole document.
        heading: The heading prefix to locate (e.g. ``"## 5."``).
        label: How to name the document in the failure message.

    Returns:
        Everything from that heading up to the next ``##`` heading.

    Raises:
        AssertionError: The heading is absent or duplicated, so a restructured
            document fails loudly here rather than silently reporting an empty
            section.
    """
    lines = _blank_fenced_regions(text).splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
    assert len(starts) == 1, (
        f"expected exactly one heading starting {heading!r} in {label}; "
        f"found {len(starts)}"
    )
    start = starts[0]
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def parse_steps(text: str, spec: PlanSpec) -> dict[str, bool]:
    """Return ``{step id: is ticked}`` for every checkbox in the steps section.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        Each checkbox step id mapped to whether its box is ticked.

    Raises:
        AssertionError: A step id appears on more than one checkbox.  The last
            one would win, so a step re-listed as unticked after it shipped
            would silently un-tick itself and every row owning it would pass --
            blinding the arm this gate exists for.  These documents re-parent
            and re-list steps routinely, so the collision is a live edit away.
    """
    steps: dict[str, bool] = {}
    body = section(text, spec.steps_heading, label=str(spec.path))
    for line in body.splitlines():
        match = _CHECKBOX_RX.match(line)
        if match is None:
            continue
        step = match.group("step")
        assert step not in steps, (
            f"step {step!r} has more than one checkbox in {spec.steps_label}; "
            "the last would win and could silently un-tick a shipped step"
        )
        steps[step] = match.group("tick").lower() == "x"
    return steps


def parse_ledger(text: str, spec: PlanSpec) -> list[tuple[str, str]]:
    """Return ``(finding id, owner cell)`` for every ledger data row.

    The header and its delimiter row are skipped; every other table row is a
    finding.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        One pair per finding row, in document order.

    Raises:
        AssertionError: A row does not have the table's column count, which
            means an unescaped ``|`` inside a cell has split it -- the row
            renders wrong, and reading its last cell as the owner would be
            reading the wrong cell.
    """
    rows = []
    body = section(text, spec.ledger_heading, label=str(spec.path))
    for line in body.splitlines():
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
        assert len(cells) == spec.ledger_columns, (
            f"ledger row {cells[0]!r} split into {len(cells)} cells, not "
            f"{spec.ledger_columns} -- an unescaped '|' inside a cell splits "
            r"the row; write it as '\|'"
        )
        rows.append((cells[0], cells[-1]))
    return rows


def stated_count_violation(text: str, spec: PlanSpec) -> str | None:
    """Return the violation when the ledger's stated row count is wrong.

    The ledger says how big it is in prose ("**The ledger stands at N rows**"),
    and that sentence is edited by hand on every step that opens or closes a
    finding.  It drifted on the balance document: it read 38 while the table
    carried 40, because a step that closed four rows and opened three updated
    the rows and not the sentence.

    The sentence is OPTIONAL in the document.  A document that states no count
    is not making a false claim, and this arm exists to catch false claims, not
    to mandate a sentence -- which is why each gate file separately asserts
    that its live document DOES state one, or the arm would be vacuous.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        The violation message, or ``None`` when the count is absent or correct.
    """
    body = section(text, spec.ledger_heading, label=str(spec.path))
    match = spec.stated_count_rx.search(body)
    if match is None:
        return None
    stated = int(match.group("count"))
    actual = len(parse_ledger(text, spec))
    if stated == actual:
        return None
    return (
        f"{spec.ledger_label} says it stands at {stated} rows and the table "
        f"carries {actual}. Update the sentence with the rows, or delete it."
    )


def arc_state_violation(text: str, spec: PlanSpec) -> str | None:
    """Return the violation when the orientation section has grown into a log.

    **This is the measured worst failure mode of a long-lived planning
    document.**  The section is meant to tell the next session where to pick
    up; on the balance README it had instead become an append-only log of
    **1,019 of that file's 6,688 lines**, and that was AFTER an extraction had
    already emptied it once.  Every session appended "X-n is DONE" with its
    measurements underneath the last one, so the reader had to scroll a month
    of history to find the branch they were on.

    A cap is what makes REPLACE the cheap option.  Under one, a session that
    wants to add a paragraph has to decide what leaves -- and the answer is
    almost always that the outgoing paragraph belonged in a step entry, a
    finding row or a standing rule, which is where the next reader would
    actually look for it.  Without one, appending is free and nothing ever
    leaves.

    Unlike :func:`line_count_violation`, whose remedy is archiving, this one's
    remedy is nearly always RELOCATION: the paragraph that pushed the section
    over is durable content, and durable content in a section defined as
    disposable is content the next session will not find.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        The violation message, or ``None`` when the section is within its cap.

    Raises:
        AssertionError: The section is missing, via :func:`section` -- a
            document that dropped it would otherwise pass this arm vacuously.
    """
    body = section(text, spec.arc_state_heading, label=str(spec.path))
    lines = len(body.splitlines())
    if lines <= spec.arc_state_cap:
        return None
    return (
        f"{spec.arc_state_heading!r} is {lines} lines against a "
        f"{spec.arc_state_cap}-line cap. It is REPLACED each session, never "
        "appended to. Move what outlived this session to where the next one "
        "will look for it: a constraint on a step -> that step's "
        f"{spec.steps_label} entry; a defect -> a {spec.ledger_label} row with "
        f"an owner; a standing rule -> {spec.rules_label}. Then overwrite "
        "what is left."
    )


def line_count_violation(text: str, spec: PlanSpec) -> str | None:
    """Return the violation when the document is over its line cap.

    A prose target does not hold a document: the balance README's previous rule
    asked for ~500 lines and exempted the record of completed work, and the
    file was measured at 6,688 lines -- an as-built extraction, three months of
    narrative and a findings ledger averaging 2,077 characters a row.  The
    exempt category has no ceiling and it is the category that grows every
    step, so the cap is on the WHOLE file with no exemption.

    **The message names the remedy, because the wrong remedy is the danger
    here.**  A cap invites trimming whatever is nearest, and what is nearest
    when this fires is the step you are writing -- the specification of work
    that has NOT happened.  The archive move is the only legal answer: condense
    a span that is DONE, where the commits still hold the detail.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        The violation message, or ``None`` when the document is within the cap.
    """
    lines = len(text.splitlines())
    if lines <= spec.line_cap:
        return None
    return (
        f"the plan document is {lines} lines against {spec.line_cap_rule}'s "
        f"{spec.line_cap}-line cap (over by {lines - spec.line_cap}). Archive "
        f"a COMPLETED span to an as-built record under {spec.archive_rule} -- "
        "one line per step, its hash and what it closed. Do not trim a live "
        "step's specification to fit; shrink the record of what is done, never "
        "the specification of what remains."
    )


def _vocabulary_violations(
    finding: str, owner: str, note: str | None, spec: PlanSpec,
) -> list[str]:
    """Return the violations for a non-step owner's stated requirement.

    The owner rule does not admit ``operator`` and ``developer-decision`` as
    bare words: an operator row states THE QUESTION, and a developer-decision
    is DATED with the options named.  Both halves are what make the value an
    answer rather than a shrug, and the dated half is mechanically checkable.

    Args:
        finding: The finding id, for the message.
        owner: The vocabulary word.
        note: Its parenthesised annotation, or ``None``.
        spec: The document's :class:`PlanSpec`.

    Returns:
        Zero or one violation message.
    """
    if not note:
        return [
            f"{finding}: owner {owner!r} carries no annotation -- "
            f"{spec.owner_rule} requires the question stated (operator) or the "
            "fork named and dated (developer-decision), not the bare word"
        ]
    if owner == "developer-decision" and not _DATED_RX.search(note):
        return [
            f"{finding}: owner 'developer-decision' is not DATED -- "
            f"{spec.owner_rule} requires the date the fork was taken; got "
            f"{note!r}"
        ]
    return []


def owner_violations(text: str, spec: PlanSpec) -> list[str]:
    """Return one message per live-owner-rule violation in the ledger.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        A list of human-readable violations; empty when every owner is live.
    """
    steps = parse_steps(text, spec)
    violations = []
    for finding, cell in parse_ledger(text, spec):
        if not cell:
            violations.append(
                f"{finding}: no owner at all ({spec.owner_rule})"
            )
            continue
        for part in split_owners(cell):
            match = OWNER_RX.match(part)
            if match is None:
                violations.append(
                    f"{finding}: owner {part!r} is not an owner -- "
                    f"{spec.owner_rule}'s column is a ' / '-separated list of "
                    "ids or vocabulary words, each optionally annotated in "
                    "parentheses"
                )
                continue
            owner, note = match.group("owner"), match.group("note")
            if owner in spec.non_step_owners:
                violations.extend(
                    _vocabulary_violations(finding, owner, note, spec)
                )
                continue
            if owner not in steps:
                violations.append(
                    f"{finding}: owner {owner!r} is not a {spec.steps_label} "
                    "checkbox -- an owner must be TICKABLE, or it is outside "
                    f"{spec.owner_rule}'s vocabulary "
                    f"{sorted(spec.non_step_owners)}"
                )
                continue
            if steps[owner]:
                violations.append(
                    f"{finding}: owner {owner!r} has SHIPPED (its box is "
                    f"ticked).  {spec.ship_rule}: a step that ships re-points "
                    "every row that named it"
                )
    return violations


def step_entries(text: str, spec: PlanSpec) -> list[tuple[str, bool, str]]:
    """Return ``(step id, is ticked, entry body)`` for every step, in order.

    A step's ENTRY runs from its checkbox line to the next checkbox, the next
    ``###`` sub-heading, or the end of the steps section -- whichever comes
    first.  The sub-heading arm is load-bearing rather than defensive: both
    documents group steps under ``###`` headings (an umbrella over decomposed
    leaves, a block of carried steps), and without it the last step before such
    a heading absorbs the whole group's prose and is measured as far longer
    than it is.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        One triple per checkbox, in document order.
    """
    lines = section(text, spec.steps_heading, label=str(spec.path)).splitlines()
    marks = [(i, m) for i, line in enumerate(lines) if (m := _CHECKBOX_RX.match(line))]
    entries = []
    for position, (index, match) in enumerate(marks):
        stop = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        for scan in range(index + 1, stop):
            if lines[scan].startswith("###"):
                stop = scan
                break
        entries.append((
            match.group("step"),
            match.group("tick").lower() == "x",
            "\n".join(lines[index:stop]).rstrip(),
        ))
    return entries


def ticked_entry_violations(text: str, spec: PlanSpec) -> list[str]:
    """Return one message per shipped step that is narrative instead of a pointer.

    **A shipped step's entry is a POINTER, not an account.**  Two requirements,
    both mechanical:

    * the entry OPENS with its commit citation -- ``- [x] **<step> -- what it
      did.** `<sha>` -- one or two sentences``;
    * the entry is at most ``spec.ticked_entry_cap`` lines.

    The reason is the balance arc's, paid for repeatedly and recorded in its
    own rule 5: **prose nobody re-verifies is worse than a hash anyone can
    check.**  That arc carried an invented provenance line, a drifted count and
    a citation to a deleted producer into records of shipped work, because a
    narrative wins no argument with the code and nobody re-reads it.  The
    commit message is the step's own account of itself and the diff is the
    truth; a live planning document only needs to say which commit to read.

    **POSITION is the discriminator, not shape, and that is load-bearing.**
    Requiring merely that the entry contain a backticked hex token would be
    satisfied by an Alembic revision id -- 12 hex characters, and both
    documents cite one beside the commit that added it.  Requiring the
    citation to be the first thing after the step's bold title cannot be
    satisfied by anything else, and it reads naturally.

    **The hash's FORM is checked, not its existence.**  Resolving it would need
    git, and CI checks out shallow (``actions/checkout`` defaults to depth 1),
    so an existence check would either fail on every historical hash there or
    be skipped exactly where it matters -- a gate that passes on nothing.  The
    author's own ``git log`` verifies the hash at the moment it is written;
    this arm catches the far commoner failure, a step ticked with a paragraph
    of prose and no hash at all.

    **Unticked steps are never touched.**  A live step is a SPECIFICATION and
    may be as long as it needs; shrinking the record of what is done is the
    whole point, and trimming what remains is the mistake the line-cap
    message warns against one level up.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        A list of human-readable violations; empty when the arm is off
        (``ticked_entry_cap is None``) or every ticked entry complies.
    """
    if spec.ticked_entry_cap is None:
        return []
    violations = []
    for step, ticked, body in step_entries(text, spec):
        if not ticked:
            continue
        opener = _TICKED_OPENER_RX.match(body.splitlines()[0])
        if opener is None or not _COMMIT_CITATION_RX.match(opener.group("rest")):
            violations.append(
                f"{step}: its box is ticked but its entry does not OPEN with "
                "its commit hash. Write it as ``- [x] **<step> -- what it "
                "did.** `<sha>` -- one or two sentences``. A future reader "
                "must be able to read the code rather than prose about it, and "
                "the position is what makes the hash unambiguous: an Alembic "
                "revision id is hex too, so 'somewhere in the entry' would let "
                "one stand in for a commit."
            )
        length = len(body.splitlines())
        if length > spec.ticked_entry_cap:
            violations.append(
                f"{step}: its box is ticked and its entry is {length} lines "
                f"against a {spec.ticked_entry_cap}-line cap. A shipped step "
                "is a POINTER, not an account of itself -- the narrative, the "
                "measurements and the review residue belong in the commit, "
                "which is where the code agrees with them. Condense to what "
                "it did, its hash, and anything a LATER step must still obey."
            )
    return violations
