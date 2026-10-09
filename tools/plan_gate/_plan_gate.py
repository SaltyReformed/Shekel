"""Shared machinery for grading an ARC DOCUMENT and for reading an owner cell.

The arc documents no longer carry a findings ledger: every finding is a
row in ``docs/plans/ledger.md`` and every step is indexed in
``docs/plans/steps.md``, both graded by :mod:`_registry`.  What is left here is
the machinery that is still genuinely per-document, plus the one primitive the
registry gate must not re-implement:

* **the arc-document arms** -- the whole-file line cap, the signpost cap and
  the shipped-step pointer rule.  These stay per-document because each document
  has its own cap, its own signpost heading and its own steps heading.
* **the owner grammar** (:data:`OWNER_RX`, :func:`split_owners`,
  :data:`NON_STEP_OWNERS`), imported by :mod:`_registry`.  It carries
  false-positive fixes measured against a real ledger, and a second copy would
  be the very denormalization this restructure removes.

Each grammar fix below would make a naive implementation report a correct row
as broken, and a gate that cries wolf is uninstalled rather than fixed:

1. an owner cell is often an ANNOTATED id (``X-i1 (the redundancy)``,
   ``X-e (widened 2026-07-27; see also N-73)``), so the id is parsed OUT of the
   cell rather than the cell being read as an id;
2. a cell can name TWO owners for two halves of one row
   (``X-j (display) / X-e (cache)``), and both must be live -- and the split is
   taken at PAREN DEPTH ZERO, or an annotation that ever contains a slash
   (``X-b (display / cache)``) is torn into two bogus owners.

Rather than scan a cell for anything id-shaped -- which would try to validate
the ``N-73`` inside ``X-e (widened 2026-07-27; see also N-73)`` -- the cell must
MATCH the owner grammar.  Anything that does not is itself a failure, with the
cell quoted.

A steps section can contain a FENCED code block, so fenced regions are blanked
before a section is carved out.  Otherwise a ``##``-prefixed line inside a fence
truncates the section and every step after it vanishes -- the ticked-entry arm
would then grade a document it has silently stopped reading.  The checkbox
grammar, the fence rule and a step's entry live in :mod:`tools.ci.arc_steps`
since X-cx's L7, which reads them too: one producer for both tools.

The rule citations are module constants rather than :class:`PlanSpec` fields.
They were per-document when each document stated its own rules; there is now
one ``conventions.md`` for every arc, so a field every caller sets identically
is the denormalization this restructure exists to remove.  That the cited rule
NUMBERS exist is itself gated, by
``test_registry_integrity.test_conventions_still_states_every_rule_the_gate_cites``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools.ci.arc_steps import (
    HeadingCountError,
    blank_fenced_lines,
    entries,
    section_span,
)

#: Where a relocated defect, and where the standing rules, now live.  Named in
#: the signpost message so the reader is sent somewhere that exists.
LEDGER_DOC = "docs/plans/ledger.md"
CONVENTIONS_DOC = "conventions.md"

#: The rules this module's messages cite, by their number in ``conventions.md``.
LINE_CAP_RULE = f"{CONVENTIONS_DOC} rule 4"
ARCHIVE_RULE = f"{CONVENTIONS_DOC} rule 5"

#: One owner: an id or vocabulary word, optionally annotated in parentheses.
#: The annotation may itself contain a parenthesised aside, which the inner
#: alternation allows without turning the whole thing into a wildcard.
OWNER_RX = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)"
    r"(?: \((?P<note>[^()]*(?:\([^()]*\)[^()]*)*)\))?$",
)

#: The non-step owner values ``ledger.md`` admits.  There is deliberately no
#: value meaning "someone will get to it".  Each carries a REQUIREMENT rule 1
#: states and :mod:`_registry` therefore checks: ``operator`` must state the
#: question, ``developer-decision`` must be dated.
NON_STEP_OWNERS = frozenset({"operator", "developer-decision"})

#: A ticked step's opening line, split into its bold title and what follows.
#: The title runs non-greedily to its own closing ``**`` so a title containing
#: backticked code (``**R6 -- Delete `payment_day`.**``) is not mistaken for
#: the citation.
_TICKED_OPENER_RX = re.compile(
    r"^\s*[-*]\s*\[[xX]\]\s*\*\*(?P<title>.*?)\*\*\s*(?P<rest>.*)$",
)

#: What a commit hash LOOKS like, shared by the two arms that grade one.  The
#: SHAPE is one fact; the POSITION each arm requires is not, which is why this
#: is a fragment rather than a whole pattern -- :data:`_COMMIT_CITATION_RX`
#: anchors it to the start of a specification's rest-of-line, and
#: :mod:`_registry` anchors it to a whole ``steps.md`` cell.  Two copies of the
#: shape would let the index and the specification disagree about what a hash
#: even is.
COMMIT_SHA = r"[0-9a-f]{7,40}"

#: The commit citation, required to be the FIRST thing after the title.
#: Position is the discriminator, not shape: an Alembic revision id is also 12
#: hex characters and would satisfy any "contains a hash" test while naming no
#: commit -- both live documents cite one in the same breath as their commit.
_COMMIT_CITATION_RX = re.compile(rf"^`({COMMIT_SHA})`")


@dataclass(frozen=True)
class PlanSpec:
    """One ARC DOCUMENT's identity, structure and caps.

    A parameter object rather than a bag of arguments: every field is read by
    more than one violation function, and threading them individually made the
    call sites unreadable.

    **The ledger fields are gone rather than inert.**  This class carried
    ``ledger_heading``, ``ledger_label``, ``ledger_columns``, ``owner_rule``,
    ``ship_rule``, ``stated_count_rx`` and ``non_step_owners`` for a ledger that
    now lives in ``ledger.md``, and ``line_cap_rule``, ``archive_rule`` and
    ``rules_label`` for rules that now live in one ``conventions.md`` -- so
    every caller set those last three to the same string.  Ten fields whose
    values no caller could vary are ten fields a reader has to check.

    Attributes:
        path: The document, resolved absolutely so the gate grades the same
            file however pytest is invoked.
        steps_heading: Heading prefix bounding the steps section
            (e.g. ``"## 5."``).  Matched on the numbered prefix, which is
            stable: no document renumbers.
        steps_label: How to name that section in a message (e.g. ``"Section
            5"``).  Still per-document: the arc documents number their steps
            sections differently, and a message naming the wrong one sends the
            reader to a section that does not hold their step.
        line_cap: The whole-document line cap.
        arc_state_heading: Heading of the short orientation section.
        arc_state_cap: Its line cap.
        ticked_entry_cap: Line cap on a TICKED step's entry, or ``None`` to
            leave the arm off.  A shipped step is a POINTER: a sentence of
            what it did and its commit hash.  ``None`` is not "no opinion" --
            it means this document has not adopted the rule yet, and the spec
            says why.
    """

    path: Path
    steps_heading: str
    steps_label: str
    line_cap: int
    arc_state_heading: str
    arc_state_cap: int
    ticked_entry_cap: int | None = None

    def read(self) -> str:
        """Return the document's full text.

        Returns:
            The file's contents, decoded as UTF-8.
        """
        return self.path.read_text(encoding="utf-8")


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


def _heading_refusal(error: HeadingCountError, label: str) -> AssertionError:
    """The gate's failure for a document whose heading is absent or doubled.

    An ``AssertionError`` naming the document, so a restructured document fails
    the gate loudly rather than being graded as an empty section.

    Args:
        error: What :func:`tools.ci.arc_steps.section_span` refused.
        label: How to name the document in the failure message.

    Returns:
        The failure to raise.
    """
    return AssertionError(error.message(label))


def _section(text: str, heading: str, *, label: str) -> str:
    """Return the body of the ``##`` section whose heading starts with *heading*.

    Fenced lines are blanked first (:func:`tools.ci.arc_steps.blank_fenced_lines`),
    so a ``##``-prefixed line inside a code sample cannot end the section early.

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
    try:
        start, stop = section_span(text, heading)
    except HeadingCountError as error:
        raise _heading_refusal(error, label) from error
    return "\n".join(blank_fenced_lines(text.splitlines())[start:stop])


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
    body = _section(text, spec.arc_state_heading, label=str(spec.path))
    lines = len(body.splitlines())
    if lines <= spec.arc_state_cap:
        return None
    return (
        f"{spec.arc_state_heading!r} is {lines} lines against a "
        f"{spec.arc_state_cap}-line cap. It is REPLACED each session, never "
        "appended to. Move what outlived this session to where the next one "
        "will look for it: a constraint on a step -> that step's "
        f"{spec.steps_label} entry; a defect -> a {LEDGER_DOC} row with an "
        f"owner; a standing rule -> {CONVENTIONS_DOC}. Then overwrite what is "
        "left."
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
        f"the plan document is {lines} lines against {LINE_CAP_RULE}'s "
        f"{spec.line_cap}-line cap (over by {lines - spec.line_cap}). Archive "
        f"a COMPLETED span to an as-built record under {ARCHIVE_RULE} -- "
        "one line per step, its hash and what it closed. Do not trim a live "
        "step's specification to fit; shrink the record of what is done, never "
        "the specification of what remains."
    )


def step_entries(text: str, spec: PlanSpec) -> list[tuple[str, bool, str]]:
    """Return ``(step id, is ticked, entry body)`` for every step, in order.

    A step's ENTRY is :func:`tools.ci.arc_steps.entries`' span, the rule's one
    home: from its checkbox line to the next checkbox, the next ``###``
    sub-heading, or the end of the steps section.  The body is that span of the
    FENCE-BLANKED text, joined and right-stripped, so its line count is the one
    the ticked-entry cap has always measured: a span includes the blank line
    before its boundary when there is one, so counting the span would charge an
    entry at the cap for it.

    Args:
        text: The whole document.
        spec: The document's :class:`PlanSpec`.

    Returns:
        One triple per checkbox, in document order.

    Raises:
        AssertionError: The steps heading is absent or duplicated
            (:func:`_heading_refusal`).
    """
    lines = blank_fenced_lines(text.splitlines())
    try:
        found = entries(text, spec.steps_heading)
    except HeadingCountError as error:
        raise _heading_refusal(error, str(spec.path)) from error
    return [
        (entry.step, entry.ticked, "\n".join(lines[entry.start:entry.stop]).rstrip())
        for entry in found
    ]


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
