"""The RULINGS registry: one table for every arc's developer decisions.

Split from :mod:`_registry` rather than added to it, for the reason this
project has ruled twice already (findings **N-152** / **N-156** / **N-201**):
a module at pylint's 1,000-line ceiling SPLITS instead of being shaved a line
at a time, and ``_registry`` stands at 978.  ``_classes``, ``_tables`` and
``_staging`` are the same move.

**Why the registry exists at all.**  Until 2026-08-27 a ruling lived in its own
arc's document, in THREE different grammars across five documents, and
``tools/plan_gate`` parsed none of them.  ``balance`` and ``bank_import``
share one byte-identical header, ``recurrence`` and ``pay_calendar`` share
``| fork | ruling |``, and ``credit_card`` uses a numbered list.  Two
consequences, both measured:

* **Ids collided and nothing could see it.**  ``R-EX`` named two rulings in
  2026-08-10 (finding **N-217**, remedied by renaming one to ``R-FA``),
  ``R-GU`` names two today, and on 2026-08-27 ``R-GW`` was minted in BOTH
  ``balance`` and ``bank_import`` on two unmerged branches by sessions that
  had each checked, a third having reserved and dropped it.  That is
  **N-367**, third instance.  A ``docs/`` grep cannot see an unmerged branch,
  which is why checking was not enough.
* **A row could be broken and stay broken.**  ``bank_import:R-FW`` carried an
  unescaped ``|`` inside a backticked string, so it read as FOUR cells in a
  three-column table: markdown truncated the rule at the pipe and
  :func:`_tables.rows_under` would have dropped the row entirely.  Nothing
  reported it because nothing read the table.  Repaired by the lift that
  created this file.

**The key is ``(arc, id)``** (conventions.md rule 10), so two arcs holding one
bare id is LEGAL here rather than a failure -- which is the only honest model,
because the ids are already in commit messages and rule 2 makes those
immutable.  What a shared id costs is a BARE citation resolving to two rules,
and that is a property of the citation; grading citations is ``X-ao-3``.

**The migration is DECLARED and graded both ways** (:func:`migration_violations`).
An arc named in the registry's preamble carries no rulings table in its own
document, and an arc not named there still carries one -- so the corpus cannot
sit in a half-moved state where a reader has to guess which document is
authoritative for which arc.  ``X-ao-2`` empties :data:`ARC_RULING_HEADINGS`
and the arm becomes "no arc document carries a rulings heading".
"""
from __future__ import annotations

import re
from collections import Counter

from _registry import ARC_DOCS, PLANS
from _tables import RULINGS_HEADER, RulingRow, rows_under

#: Every arc's rulings, once :data:`ARC_RULING_HEADINGS` is empty.
RULINGS = PLANS / "rulings.md"

#: Rule 3's self-count, anchored on the LIVE wording.  A pattern that matches
#: nothing reads as "no count is claimed" and passes, which is the failure the
#: balance README's own row-count arm shipped with -- see
#: :data:`_registry.STATED_COUNT_RX`, whose sibling this is.
STATED_COUNT_RX = re.compile(
    r"\*\*The ruling registry stands at (?P<count>\d+) rows?\.?\*\*"
)

#: The preamble sentence naming which arcs have moved.  Graded rather than
#: trusted: :func:`migration_violations` reconciles it against both the
#: registry's own rows and the arc documents.
MIGRATED_RX = re.compile(
    r"\*\*Arcs whose rulings live here: (?P<arcs>[^.]+)\.\*\*"
)

#: A ruling id, as every arc spells one.  ``R-`` then upper-case letters
#: (``R-G``, ``R-FW``) or the two arcs that number theirs (``R-R38``,
#: ``R-PC1``).  Anchored whole, so an annotated cell is rejected here and
#: stripped by :attr:`_tables.RulingRow.bare_ident` first.
IDENT_RX = re.compile(r"^R-[A-Z]+\d*$")

#: Where an arc that has NOT yet moved still states its rulings, by the heading
#: that introduces them.  A map rather than a search for "a table that looks
#: like rulings", because the four grammars have nothing in common to search
#: for -- which is the defect this registry removes.
#:
#: **This map SHRINKS to empty and is deleted with the last entry.**  It is
#: migration state, not a permanent allowlist: an arc leaves it in the same
#: commit that lifts its rulings, and :func:`migration_violations` reads it in
#: both directions so an entry that outlives its table fails.
#: Every header a rulings TABLE is spelled with, across all three grammars.
#:
#: **The residual-table arm reads all of them, not just the one the migrated
#: arcs used.**  Its first draft matched only ``| ruling | date |`` -- which is
#: the spelling of ``balance`` and ``bank_import``, the two arcs that have
#: already moved and no longer have a table -- so it was structurally blind to
#: the exact three arcs ``X-ao-2`` must protect.  Two independent adversarial
#: reviews demonstrated the same future commit passing every arm with two live
#: copies of one arc's rulings.
RULING_TABLE_HEADERS = (
    "| ruling | date | what was ruled |",
    "| fork | ruling |",
)

ARC_RULING_HEADINGS = {
    "recurrence": "## Rulings",
    "pay_calendar": "## Rulings",
    "credit_card": "## Locked developer rulings",
}

#: The number of rows that can only be an accident.
#:
#: **Not a forcing function.**  Rule 4 gives ``ledger.md`` and ``steps.md`` no
#: LINE cap on one argument the developer accepted twice on 2026-08-25: a cap
#: on a registry holding ONE LINE PER THING caps how many of that thing the
#: project may have, and a gate may not refuse to record a defect somebody has
#: measured.  A ruling is the same shape -- a decision somebody has TAKEN --
#: so this file is capped the same way its siblings are, which is not at all.
#: This is the runaway backstop a dropped cap owes: a duplicated table or a
#: generator loop fails loudly instead of committing.  Set far above the 182
#: rulings the five arcs held when the registry was created (105 lifted, 77
#: still to come), so it can never bind on real work.
RULINGS_RUNAWAY_ROWS = 600

#: The widest a single ruling row may be, in characters.
#:
#: **Rule 4's whole content for this file, and the reason the line cap could
#: go.**  The developer's 2026-08-25 rulings on ``ledger.md`` and ``steps.md``
#: were a SWAP and not a removal: the line cap went BECAUSE a per-row cap
#: replaced it, and ``_registry.LEDGER_ROW_CAP``'s own note says it "is now the
#: whole of rule 4 for this file -- it is the arm that actually prevents the
#: failure the line cap was reached for, a row swelling into the arc document's
#: argument".  The first draft of this registry took the first half of that
#: precedent and not the second; an adversarial review measured what that cost
#: and the developer ruled the cap comes across.
#:
#: Set to the ledger's own 2,000 rather than to a number that fits today's
#: file, which is what rule 4 forbids.
RULINGS_ROW_CAP = 2000

#: How many LIFTED rows exceed :data:`RULINGS_ROW_CAP` on arrival.
#:
#: **A measured debt, not an exemption.**  These 23 rows were over the cap in
#: the arc documents that held them and were lifted verbatim -- rule 5 forbids
#: trimming a live specification to fit.  The overflow's remedy is rule 4's
#: own: it goes to the OWNING STEP's specification, never deletion, and that
#: is ``X-ao-2``'s work because that is the step that finishes the corpus.
#:
#: The number is here so the arm can distinguish "the debt we recorded" from
#: "a NEW row over the cap", which is the only thing a fence would have hidden.
#: It only ever goes DOWN; ``X-ao-2`` takes it to zero and deletes it.
LIFTED_ROWS_OVER_CAP = 23


def ruling_rows() -> list[RulingRow]:
    """Return every ruling the registry states, in table order.

    Returns:
        One :class:`_tables.RulingRow` per body row.
    """
    return [
        RulingRow(*cells)
        for cells in rows_under(RULINGS.read_text(), RULINGS_HEADER)
    ]


def migrated_arcs() -> list[str]:
    """Return the arcs the registry DECLARES it holds the rulings for.

    Returns:
        The arc slugs named in the preamble, in the order it names them.
    """
    match = MIGRATED_RX.search(RULINGS.read_text())
    if match is None:
        return []
    return re.findall(r"`([a-z_]+)`", match.group("arcs"))


def stated_count_violation() -> str | None:
    """Return rule 3's violation for this registry, or ``None``.

    A registry that states its own size has that number CHECKED.  Every one of
    ``steps.md``'s four self-counts was stale by the time its arm was written,
    which is what this rule says about itself.

    Returns:
        The message, or ``None`` when the stated count matches the table.
    """
    text = RULINGS.read_text()
    match = STATED_COUNT_RX.search(text)
    if match is None:
        return (
            "rulings.md states no row count -- conventions.md rule 3 requires "
            "`**The ruling registry stands at N rows.**`, and a sentence the "
            "gate cannot find reads as no claim at all rather than as a "
            "failure"
        )
    stated, actual = int(match.group("count")), len(ruling_rows())
    if stated == actual:
        return None
    return (
        f"rulings.md says it stands at {stated} rows and the table holds "
        f"{actual} (conventions.md rule 3)"
    )


def runaway_violation() -> str | None:
    """Return the backstop's message, or ``None``.

    Returns:
        The message when the table holds more rows than any real corpus could.
    """
    actual = len(ruling_rows())
    if actual <= RULINGS_RUNAWAY_ROWS:
        return None
    return (
        f"rulings.md holds {actual} rows against a {RULINGS_RUNAWAY_ROWS}-row "
        f"runaway backstop. This is not a forcing function and rule 5 is not "
        f"the answer: a count this size is a duplicated table or a generator "
        f"loop, not work somebody did"
    )


def row_width_violations() -> list[str]:
    """Return every row wider than :data:`RULINGS_ROW_CAP`.

    Rule 4 on this file, in full.  The widest row lifted is
    ``bank_import:R-GD`` at 16,087 characters against a 529-character median,
    which is the arc document's argument living in the registry -- the exact
    sentence ``_registry.LEDGER_ROW_CAP`` was written for, at 4.5x the worst
    instance that forced it there.

    Returns:
        One message per over-cap row, widest first.
    """
    over = sorted(
        (row for row in ruling_rows() if row.width > RULINGS_ROW_CAP),
        key=lambda row: row.width, reverse=True,
    )
    return [
        f"{row.key} is {row.width} characters against the "
        f"{RULINGS_ROW_CAP}-character row cap. conventions.md rule 4: the "
        f"overflow goes to the OWNING STEP's specification, never deletion"
        for row in over
    ]


def declared_arc_counts() -> dict[str, int]:
    """Return how many rulings the preamble CLAIMS each migrated arc has.

    Rule 3's sentence, per arc.  The total alone cannot catch a lift that
    drops rows: a merge resolving "take ours" against the branch that deletes
    an arc document's table silently loses whatever that branch added, and the
    total stays right if the same merge corrects it.  Per-arc counts are what
    make that resolution fail loudly, and the failure direction matters --
    ``project_registry_merges_hide_in_clean_hunks``.

    Returns:
        ``{arc: count}`` as the preamble states it.
    """
    match = MIGRATED_RX.search(RULINGS.read_text())
    if match is None:
        return {}
    return {
        arc: int(count)
        for arc, count in re.findall(r"`([a-z_]+)`\s+(\d+)", match.group("arcs"))
    }


def key_violations() -> list[str]:
    """Return every way a row fails to name exactly one ruling.

    Six arms, and each has cost this corpus something real:

    * **A duplicate ``(arc, id)``** is the collision **N-217** and **N-367**
      record, now inside one table where it is visible.
    * **An unknown arc** would file a ruling under a slug no document answers
      to, which is rule 10's key with half of it invented.
    * **A malformed id** cannot be cited, and a ruling nobody can cite is what
      32 of ``pay_calendar``'s 33 rows are today -- only ``R-PC1`` carries an
      id of its own.
    * **An empty date** loses the one fact that orders two rulings on one
      subject -- ``R-R3``'s subtype is superseded BY ``R-R13``, and only the
      dates say which way round.
    * **An empty rule** is a row that states no rule, which is the second half
      of what ``X-ao`` was registered to grade.  (Its index row still cites
      **N-220** for this; that finding CLOSED on 2026-08-14 at ``d8aed644``
      and the stale citation is corrected with the decomposition.)
    * **An ``also`` id that is also a primary id** would make one citation
      resolve to two rows inside a single arc -- the collision this registry
      exists to end, arriving through the alias column instead.

    Returns:
        One message per violation, in table order.
    """
    rows = ruling_rows()
    problems: list[str] = []

    keys = Counter(row.key for row in rows)
    for key, count in keys.items():
        if count > 1:
            problems.append(
                f"{key} names {count} rulings -- the key is (arc, id) and it "
                f"is unique within the registry (conventions.md rule 10)"
            )

    for row in rows:
        if row.arc not in ARC_DOCS:
            problems.append(
                f"{row.arc}:{row.bare_ident} names the arc {row.arc!r}, which "
                f"is not one of {sorted(ARC_DOCS)}"
            )
        if not IDENT_RX.match(row.bare_ident):
            problems.append(
                f"{row.arc}:{row.ident!r} is not a citable ruling id -- the "
                f"grammar is `R-` then letters, optionally numbered"
            )
        if not row.date.strip() or row.date.strip() == "--":
            problems.append(
                f"{row.key} states no date, so nothing orders it against a "
                f"ruling on the same subject"
            )
        if not row.rule.strip() or row.rule.strip() == "--":
            problems.append(f"{row.key} states no rule (N-220)")

    aliases = Counter(
        alias for row in rows for alias in row.also_keys()
    )
    primary = set(keys)
    for alias, count in aliases.items():
        if alias in primary:
            problems.append(
                f"{alias} is recorded as an `also` id and is ALSO a row's own "
                f"id, so a citation of it resolves to two rulings in one arc"
            )
        if count > 1:
            problems.append(
                f"{alias} is claimed as an `also` id by {count} rows"
            )
    return problems


def _registry_side_violations(declared: list[str]) -> list[str]:
    """Return the disagreements visible in the REGISTRY alone.

    Four arms: an arc declared with no rows, rows with no declaration, an arc
    named by neither side, and a per-arc count that does not match.

    Args:
        declared: The arcs the preamble names.

    Returns:
        One message per disagreement.
    """
    counts = declared_arc_counts()
    rows = ruling_rows()
    present = {row.arc for row in rows}
    actual = Counter(row.arc for row in rows)
    problems: list[str] = []
    for arc in declared:
        if arc not in present:
            problems.append(
                f"rulings.md declares it holds {arc}'s rulings and carries no "
                f"{arc} row"
            )
        elif counts.get(arc) != actual[arc]:
            problems.append(
                f"rulings.md declares {counts.get(arc)} {arc} rulings and "
                f"carries {actual[arc]} (conventions.md rule 3, per arc). A "
                f"merge that resolves an arc document's deleted table as "
                f"'take ours' drops whatever the other side added, and the "
                f"TOTAL alone cannot see it"
            )
    for arc in sorted(present - set(declared)):
        problems.append(
            f"rulings.md carries {arc} rows and its preamble does not declare "
            f"{arc} moved, so a reader cannot tell which document is "
            f"authoritative for that arc"
        )
    for arc in sorted(ARC_DOCS):
        if arc not in declared and arc not in ARC_RULING_HEADINGS:
            problems.append(
                f"{arc} is neither declared moved nor listed in "
                f"ARC_RULING_HEADINGS, so nothing says where its rulings "
                f"live -- an arc dropped from the map without being lifted "
                f"is invisible to every other arm here"
            )
    return problems


def _document_side_violations(declared: list[str]) -> list[str]:
    """Return the disagreements only the ARC DOCUMENTS can show.

    Three arms, and they are the half a declaration checked against itself can
    never cover -- the derived-value-beside-no-reconciler shape three of these
    arcs exist to remove.  A moved arc must carry no rulings table IN ANY
    GRAMMAR and must name its new home; an unmoved arc must still carry its
    own heading.

    Args:
        declared: The arcs the preamble names.

    Returns:
        One message per disagreement.
    """
    problems: list[str] = []
    for arc, heading in sorted(ARC_RULING_HEADINGS.items()):
        if arc in declared:
            problems.append(
                f"{arc} is declared moved and still has an entry in "
                f"ARC_RULING_HEADINGS, which is the map of arcs that have NOT "
                f"moved -- an arc leaves it in the commit that lifts its "
                f"rulings"
            )
            continue
        text = ARC_DOCS[arc].read_text()
        if not any(line.startswith(heading) for line in text.splitlines()):
            problems.append(
                f"{arc} has not moved and its document carries no {heading!r} "
                f"heading, so its rulings are recorded nowhere"
            )
    for arc in declared:
        text = ARC_DOCS[arc].read_text()
        lines = [line.strip() for line in text.splitlines()]
        problems.extend(
            f"{arc}'s rulings moved to rulings.md and its own document still "
            f"carries a {header!r} table -- two copies of one registry is the "
            f"denormalization these files remove"
            for header in RULING_TABLE_HEADERS if header in lines
        )
        if "rulings.md" not in text:
            problems.append(
                f"{arc}'s rulings moved and its document does not name "
                f"rulings.md, so the section a reader lands on states no "
                f"decisions and points nowhere"
            )
    return problems


def migration_violations() -> list[str]:
    """Return every disagreement about WHICH document holds an arc's rulings.

    Seven arms over two halves, which together make "half moved" a state the
    corpus cannot hold.  The split is by what each half can SEE -- the
    registry alone, or the arc documents beside it -- rather than to satisfy a
    branch ceiling.

    **Two of the arms exist because the first draft did not close the hole
    this docstring claimed it closed**, and two adversarial reviews
    demonstrated it the same way: declare an arc moved, lift ONE of its
    rulings, leave its own table in place, and every arm passed.  The
    residual-table arm now reads every grammar in
    :data:`RULING_TABLE_HEADERS` rather than only the one the already-migrated
    arcs used, and :func:`declared_arc_counts` reconciles HOW MANY rulings
    each arc contributed rather than merely that it contributed one.

    Returns:
        One message per disagreement.
    """
    declared = migrated_arcs()
    return _registry_side_violations(declared) + _document_side_violations(declared)
