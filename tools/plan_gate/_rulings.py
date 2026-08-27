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

**The migration is FINISHED and graded both ways** (:func:`migration_violations`).
Every arc is named in the registry's preamble, and no arc document states a
ruling -- so the corpus cannot sit in a half-moved state where a reader has to
guess which document is authoritative for which arc.

**What ``X-ao-2a`` deleted matters as much as what it moved.**  The half-moved
state used to be described by ``ARC_RULING_HEADINGS``, a map of the arcs that
had not come across yet, read in both directions.  That was correct migration
state and it is now gone, because a map has to be REMEMBERED: an arc dropped
from it, or a sixth arc never added to it, is invisible to every arm reading
it.  What replaced it is a property of the TEXT -- :data:`RULING_DECLARATION_RX`
and :data:`RULINGS_HEADING_RX` -- and that choice is **N-367**'s own lesson,
which refuted a class of remedy rather than merely doubting it: a control whose
content is *the session checks harder* is measured false.

The map also had a hole its own docstring did not mention.  Its residual-table
arm read table HEADERS, and ``credit_card`` stated its eight locked rulings as
a numbered LIST -- so that arc could have kept its whole registry through the
lift with every arm green.  The pointer-section arm is the answer, and what it
cannot see is stated at :data:`RULINGS_HEADING_RX` rather than left implied.
"""
from __future__ import annotations

import re
from collections import Counter

from _plan_gate import _blank_fenced_regions
from _registry import ARC_DOCS, PLANS
from _tables import RULINGS_HEADER, RulingRow, rows_under

#: Every arc's rulings, since ``balance:X-ao-2a`` finished the lift.
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

#: Every header a rulings TABLE is spelled with, across all three grammars.
#:
#: **The residual-table arm reads all of them, not just the one the migrated
#: arcs used.**  Its first draft matched only ``| ruling | date |`` -- the
#: spelling of ``balance`` and ``bank_import``, the two arcs that had already
#: moved and so no longer had a table -- and was therefore structurally blind
#: to the exact three arcs ``X-ao-2a`` had to protect.  Two independent
#: adversarial reviews demonstrated one commit passing every arm with two live
#: copies of one arc's rulings.
RULING_TABLE_HEADERS = (
    "| ruling | date | what was ruled |",
    "| fork | ruling |",
)

#: What a ruling DECLARATION looks like, in any arc document.
#:
#: **This replaced ``ARC_RULING_HEADINGS``, and the difference is the point.**
#: That was a map of the arcs that had not moved yet -- migration state, read
#: in both directions, correct while it lasted and DELETED by ``X-ao-2a`` with
#: its last entry.  A map has to be remembered: an arc dropped from it, or a
#: sixth arc never added, is invisible to every arm that reads it.  A SHAPE
#: does not.  ``N-367``'s own lesson is that a control whose content is *the
#: session checks harder* is measured false, so what replaces the map is a
#: property of the TEXT.
#:
#: Three spellings.  The first two are taken from the corpus; the third was
#: constructed by an adversarial review, which wrote six future commits that
#: state a ruling in an arc document and measured which the gate still let
#: through.  The comma and the two verbs were both accidents of how the
#: corpus happened to be written: ``R-CC14, taken 2026-09-01`` and
#: ``R-CC15 ruled 2026-09-01`` (no comma) both passed.
#:
#: * the closing clause the ``recurrence`` table used, ``R-R33, ruled
#:   2026-08-25 (developer)`` -- 26 of its 37 rows;
#: * the PROSE form, ``**Ruling R-R28 (2026-08-19): ...**``, which is how one
#:   live ruling came to sit outside every table for eight days while
#:   ``steps.md`` cited it as ``R13``'s;
#:
#: **A DATE beside an id is NOT enough either**, and trying it was measured:
#: ``**R-R30 (2026-08-19) decides the FORM READ path and nothing else**`` is a
#: live CITATION in the recurrence document that restates its ruling's date,
#: and an ``R-xx (DATE`` alternative reported it.  So the first widening made
#: the VERB the discriminator and turned a live citation into a declaration;
#: the second made the DATE the discriminator and did it again.
#:
#: **The arm that WOULD close this needs no wording at all: an arc document
#: may name no ruling id that has no `rulings.md` row.**  A new ruling written
#: in an arc document carries an id, and that id is either in the registry --
#: in which case it is recorded -- or it is not.  It cannot be built yet:
#: **N-376** measures 88 ids cited from live files with no row, because an
#: archived ruling's text stays in its archive, so the arm would fire on a
#: dozen legitimate citations today.  ``X-ao-3`` is where that resolves, and
#: this pattern is what holds until it does.
#:
#: **What still passes, stated rather than left to be discovered.**  An
#: ID-LESS dated block of developer decisions under a heading that does not
#: say ``rulings`` -- ``## Locked architecture decisions (developer,
#: 2026-09-01)`` -- is caught by NOTHING here, and it is ``credit_card``'s
#: exact historical shape with one word changed.  Nor is a new table headed
#: ``| id | date | decision |`` under ``## Decisions``.  A ruling is a
#: decision with an ID and a DATE; a dated decision with no id is not yet
#: one, and no property of the TEXT separates it from ordinary argument.
#: That is a real limit and it is the reason the pointer-section arms exist
#: beside this one rather than instead of it.
#:
#: **A CITATION is deliberately not matched, and the DATE is the whole
#: discriminator.**  ``ruling **R-R35**`` and ``**Ruling R-AP, taken AGAINST
#: the recommendation**`` name a ruling without stating one, and both are live
#: text in arc documents today.  A first attempt at widening this pattern made
#: the VERB the discriminator and admitted ``taken`` -- which turned that
#: second live citation into a reported declaration, and five controls went
#: red at once.  A verb must therefore be followed by a DATE; only
#: ``archived`` stands alone, because no citation is written that way.
RULING_DECLARATION_RX = re.compile(
    r"\bR-[A-Z]+\d*[,;]?\s*(?:ruled|taken|decided|agreed)\s+(?:on\s+)?"
    r"\(?\d{4}-\d{2}-\d{2}"
    r"|\bR-[A-Z]+\d*\s*[,;]?\s*archived\b"
    r"|\*\*Ruling\s+R-[A-Z]+\d*\s*[(,]\s*\d{4}-\d{2}-\d{2}"
)

#: A heading whose SUBJECT is the arc's rulings, as opposed to one that merely
#: cites a ruling in passing.
#:
#: Anchored at the START of the heading text, after any section number, so
#: ``## Rulings``, ``## The rulings`` and ``## 4. The rulings`` match while
#: ``## 0. Why this arc splits, and the one ruling still owed`` and
#: ``### Phase X -- the anchor half (ruling R-EB)`` do not.  All four are live
#: headings; the anchoring is what tells them apart.
#:
#: A ``locked\s+developer\s+`` alternative was carried for ``credit_card``'s
#: old heading and DELETED: that heading is gone, no live document uses the
#: form, and a surviving mutant proved nothing would notice if the branch
#: stopped working.  The optional ``the`` is ``?`` rather than ``*`` for the
#: same reason -- ``## the the the rulings`` matched, and nothing wanted it to.
#:
#: **The heading is part of the convention, deliberately.**  Every arc document
#: carries a rulings POINTER section and this pattern is what finds it, so
#: renaming that heading fails loudly rather than silently unhooking the two
#: arms that read the section.  ``balance`` had exactly that hole until
#: ``X-ao-2a``: its section was headed ``## 4. Decisions that govern the
#: remaining work``, which no anchored pattern matches, so an arm grading five
#: arcs would have graded four and reported on five.
#:
#: **What this arm can and cannot see, stated rather than implied.**  It
#: catches a rulings SECTION that has stopped being a pointer -- which is how
#: an ID-LESS block of decisions gets written, and ``credit_card``'s eight sat
#: in exactly that shape, under exactly such a heading, unparsed for five
#: weeks.  It does NOT catch a ruling written with no id under a heading that
#: names something else; nothing structural could, and saying so here is
#: cheaper than discovering it later.
RULINGS_HEADING_RX = re.compile(
    r"^#{2,4}\s+(?:\d+[a-z]?\.\s+)?(?:the\s+)?rulings?\b",
    re.IGNORECASE,
)

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

#: The LIFTED rows that exceed :data:`RULINGS_ROW_CAP`, keyed BY WIDTH.
#:
#: **A measured debt, not an exemption.**  These rows were over the cap in the
#: arc documents that held them and were lifted verbatim -- rule 5 forbids
#: trimming a live specification to fit.  The overflow's remedy is rule 4's
#: own and the developer ruled its destination on 2026-08-27 (**R-HD**): a
#: SHIPPED ruling's deliberation goes to the as-built record of the step that
#: shipped it, an OPEN one's to that step's live specification.  That is
#: ``X-ao-2b``, and it deletes this map.
#:
#: **A WIDTH per key, and each widening was a repair rather than a refactor.**
#: ``LIFTED_ROWS_OVER_CAP = 23`` could not tell 23 rows from a different 23:
#: trimming one row while another swelled past the cap left the total at 23
#: and the arm green, which is a gate that has stopped measuring what it
#: names.  Keying it fixed that and left a second hole an adversarial review
#: then measured: membership alone lets ``bank_import:R-GD`` grow from 16,087
#: to 32,000 with every arm still green, while this note claimed "both
#: directions fail".  The WIDTH closes it, and it hands ``X-ao-2b`` a progress
#: number -- 43,855 characters of overflow -- instead of a row count.
#:
#: ``recurrence:R-R37`` and ``R-R38`` are the 24th and 25th and arrived with
#: the ``X-ao-2a`` lift, over in the document that held them exactly as the
#: other 23 were -- ``R-R38`` on the merge, having landed on ``dev`` while the
#: lift was being written.
LIFTED_ROWS_OVER_CAP = {
    "balance:R-FI": 2749, "balance:R-FJ": 2451, "balance:R-FK": 4413,
    "balance:R-FL": 2858, "balance:R-FN": 2237, "balance:R-FO": 2426,
    "balance:R-FQ": 2505, "balance:R-FR": 2842, "balance:R-GV": 2322,
    "bank_import:R-FU": 3229, "bank_import:R-FV": 3585,
    "bank_import:R-FW": 3357, "bank_import:R-FX": 2214,
    "bank_import:R-FY": 4237, "bank_import:R-GA": 3885,
    "bank_import:R-GB": 3238, "bank_import:R-GC": 2185,
    "bank_import:R-GD": 16095, "bank_import:R-GF": 6101,
    "bank_import:R-GG": 5160, "bank_import:R-GJ": 2872,
    "bank_import:R-GU": 3967, "bank_import:R-GW": 4784,
    "recurrence:R-R37": 2030, "recurrence:R-R38": 2121,
}


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
    """Return every disagreement between the over-cap rows and the DEBT.

    Rule 4 on this file, in full.  The widest row lifted is
    ``bank_import:R-GD`` at 16,087 characters against a **542.5-character
    median over the 190 rows the registry holds since ``X-ao-2a``** -- it was
    529 over 105, and the lift that moved it is the change that left the
    number standing.  That row is the arc document's argument living in the
    registry, the exact sentence ``_registry.LEDGER_ROW_CAP`` was written
    for, at 4.5x the worst instance that forced it there.

    **THREE directions, because the count this replaced had one and the
    keyed set had two.**  A new row over the cap is a failure; a row in
    :data:`LIFTED_ROWS_OVER_CAP` that is no longer over it is a failure, which
    is what tells ``X-ao-2b`` it has finished one; and a debt row that has
    GROWN is a failure, which membership alone could not see -- an adversarial
    review measured that ``bank_import:R-GD`` could go from 16,087 to 32,000
    with every arm green while this docstring claimed both directions failed.
    A row that has SHRUNK but is still over the cap fails too, and its remedy
    is to record the new width: that is a debt being paid down in public.

    Returns:
        One message per over-cap row not in the debt, widest first; then one
        per debt entry no longer over the cap; then one per debt entry whose
        width has moved.
    """
    over = {row.key: row for row in ruling_rows() if row.width > RULINGS_ROW_CAP}
    problems = [
        f"{row.key} is {row.width} characters against the "
        f"{RULINGS_ROW_CAP}-character row cap. conventions.md rule 4: the "
        f"overflow goes to the as-built record of the step that shipped the "
        f"ruling, or to that step's live specification when it has not, never "
        f"to deletion"
        for row in sorted((r for k, r in over.items()
                           if k not in LIFTED_ROWS_OVER_CAP),
                          key=lambda r: r.width, reverse=True)
    ]
    problems.extend(
        f"{key} is recorded in LIFTED_ROWS_OVER_CAP and is no longer over the "
        f"{RULINGS_ROW_CAP}-character cap. The debt is recorded row by row so "
        f"that finishing one is visible: drop this key"
        for key in sorted(set(LIFTED_ROWS_OVER_CAP) - set(over))
    )
    problems.extend(
        f"{key} is {over[key].width} characters and the debt records "
        f"{LIFTED_ROWS_OVER_CAP[key]}. A debt row may be TRIMMED (record the "
        f"new width, or drop the key once it is under the cap) and may not "
        f"GROW: membership alone let a 16,087-character row reach 32,000 with "
        f"every arm green"
        for key in sorted(set(LIFTED_ROWS_OVER_CAP) & set(over))
        if over[key].width != LIFTED_ROWS_OVER_CAP[key]
    )
    return problems


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
    that neither declares nor is declared, and a per-arc count that does not
    match.

    **The fourth arm used to admit an arc that had not moved yet.**  That
    exemption is gone with ``ARC_RULING_HEADINGS``: every arc in
    :data:`_registry.ARC_DOCS` states its rulings here, so an arc missing from
    the preamble is a failure rather than a pending migration -- which also
    makes a SIXTH arc, added later and never lifted, fail on the day it is
    added instead of silently keeping its own copy.

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
    problems.extend(
        f"{arc} is an arc document and rulings.md does not declare it, so "
        f"nothing says where its rulings live. Since balance:X-ao-2a there is "
        f"no half-moved state to be in: every arc states its rulings here"
        for arc in sorted(set(ARC_DOCS) - set(declared))
    )
    return problems


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Return ``(heading line, body lines)`` for every ``##``-or-deeper heading.

    A section's body runs to the next heading of the SAME OR SHALLOWER level,
    so a subsection travels with the section it sits inside rather than ending
    it.  That matters here: a rulings POINTER that grew a ``### ...`` block of
    decisions underneath must read as part of the pointer, or the arm refusing
    a list under this heading looks at the wrong lines.

    Args:
        text: The whole document.

    Returns:
        One pair per heading, in document order.
    """
    heads: list[tuple[int, str, list[str]]] = []
    for line in text.splitlines():
        after = line.lstrip("#")
        level = len(line) - len(after)
        if level and after.startswith(" "):
            heads.append((level, line, []))
        elif heads:
            heads[-1][2].append(line)
    out: list[tuple[str, list[str]]] = []
    for i, (level, heading, own) in enumerate(heads):
        body = list(own)
        for deeper_level, deeper_heading, deeper_own in heads[i + 1:]:
            if deeper_level <= level:
                break
            body.append(deeper_heading)
            body.extend(deeper_own)
        out.append((heading, body))
    return out


def _document_side_violations() -> list[str]:
    """Return every way an ARC DOCUMENT still states a ruling.

    FIVE arms, over EVERY arc document rather than over a map of the ones
    that have not moved.  A map is migration state and it was right while the
    migration ran; a shape is what survives the migration ending, because
    nothing has to remember to keep it up to date.

    * **No rulings TABLE**, in any of the grammars the corpus used.
    * **No ruling DECLARATION** -- the closing clause or the prose form.  This
      is the arm that would have caught ``recurrence:R-R28``, a live ruling
      that sat in section 4 prose while ``steps.md`` cited it.
    * **A rulings SECTION EXISTS** in every arc document, found by an
      anchored heading pattern -- so renaming that heading fails loudly
      rather than silently unhooking the two arms that read the section.
    * **That section is a POINTER**: it names ``rulings.md``.
    * **And it states no decisions**: no table row and no list item of any
      kind.  The bold-led form was the first draft's, and ``credit_card``'s
      eight happened to be bold; nothing makes the next block bold.  This is
      the arm for an ID-LESS block, which is the shape those eight had and
      which no id- or table-based arm can see.

    **A SIXTH arm was DELETED rather than kept**, and the mutation run is why:
    "the document names ``rulings.md`` somewhere" and "the pointer section
    names it" both survived one control, because the control that removed
    every mention fired both -- so either could have been dead and the suite
    green.  The section arms subsume it, and each of the four now has a
    control that fails when only that arm is neutralised.

    Returns:
        One message per disagreement.
    """
    problems: list[str] = []
    for arc in sorted(ARC_DOCS):
        # Fenced regions are BLANKED first, which this module was the third
        # caller to need and the only one not to do.  Both directions were
        # measured on the live `credit_card` document: a `# ...` line inside a
        # fence ended the pointer section early, so three ID-LESS rulings
        # written after it were invisible to every arm here; and a fenced
        # EXAMPLE of a forbidden declaration was reported as a declaration, so
        # a document could not illustrate the shape it must not use.
        # `_registry.arc_checkboxes` and `_plan_gate._section` already carry
        # this in prose, and `_duplication` already imports the helper.
        text = _blank_fenced_regions(ARC_DOCS[arc].read_text())
        lines = text.splitlines()
        problems.extend(
            f"{arc}'s document carries a {header!r} table and its rulings are "
            f"in rulings.md -- two copies of one registry is the "
            f"denormalization these files remove"
            for header in RULING_TABLE_HEADERS
            if header in [line.strip() for line in lines]
        )
        problems.extend(
            f"{arc}'s document DECLARES a ruling at line {n}: {line.strip()[:80]!r}. "
            f"A ruling is stated once, in rulings.md, keyed (arc, id); an arc "
            f"document CITES one"
            for n, line in enumerate(lines, 1)
            if RULING_DECLARATION_RX.search(line)
        )
        pointers = [(heading, body) for heading, body in _sections(text)
                    if RULINGS_HEADING_RX.match(heading)]
        if not pointers:
            problems.append(
                f"{arc}'s document carries no rulings section, so a reader "
                f"landing in it is told nothing about where its decisions are. "
                f"Every arc carries one, and it is a POINTER"
            )
        for heading, body in pointers:
            if "rulings.md" not in "\n".join(body):
                problems.append(
                    f"{arc}'s {heading.strip()!r} section does not name "
                    f"rulings.md, so the section a reader lands on states no "
                    f"decisions and points nowhere"
                )
            stated = [b for b in body
                      if b.lstrip().startswith("|")
                      or re.match(r"^\s*(?:\d+\.|[-*])\s+\S", b)]
            if stated:
                problems.append(
                    f"{arc}'s {heading.strip()!r} section states {len(stated)} "
                    f"ruling-shaped rows or list items; it is a POINTER, and a "
                    f"list under this heading is how an ID-LESS ruling block "
                    f"survives a lift with every other arm green"
                )
    return problems


def unmapped_arc_document_violations() -> list[str]:
    """Return every arc document on disk that :data:`ARC_DOCS` does not name.

    **This is the half of the map that survived.**  ``ARC_RULING_HEADINGS``
    listed the arcs whose rulings had not moved and ``X-ao-2a`` deleted it, and
    the three pointer sections said the gate "cannot go blind to one nobody
    remembered to add".  An adversarial review measured that false: both halves
    of :func:`migration_violations` iterate ``ARC_DOCS``, a hardcoded dict, so
    a SIXTH ``implementation_plan_*.md`` created and never added to it is
    invisible to every arm in this module -- the per-arc map was gone and the
    map of WHICH DOCUMENTS ARE ARCS was not.

    ``_archive.archived_docs`` already globs ``docs/**/*.md``, so the technique
    was in the package and simply was not used here.

    Returns:
        One message per plan document that no arc slug maps to.
    """
    mapped = {path.resolve() for path in ARC_DOCS.values()}
    return [
        f"{path.relative_to(PLANS.parent.parent)} is an arc plan document and "
        f"no ARC_DOCS entry names it, so every arm in this module passes over "
        f"it -- which is what an arc dropped from the map used to be"
        for path in sorted(PLANS.glob("implementation_plan_*.md"))
        if path.resolve() not in mapped
    ]


def migration_violations() -> list[str]:
    """Return every disagreement about WHERE an arc's rulings are stated.

    Two halves, split by what each can SEE: the registry alone, or the arc
    documents beside it.

    **Three of these arms exist because a draft did not close the hole its own
    docstring claimed it closed.**  Two adversarial reviews broke the first one
    the same way -- declare an arc moved, lift ONE of its rulings, leave its
    table in place, and every arm passed -- so the table arm reads every
    grammar and :func:`declared_arc_counts` reconciles HOW MANY each arc
    contributed.  ``X-ao-2a`` then measured a third: the table arm could not see
    ``credit_card``'s numbered LIST at all, so that arc's eight rulings could
    have survived the lift whole.  Its answer is the pointer-section arm, and
    the map the whole thing keyed on is deleted.

    Returns:
        One message per disagreement.
    """
    return (_registry_side_violations(migrated_arcs())
            + _document_side_violations()
            + unmapped_arc_document_violations())
