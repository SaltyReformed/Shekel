"""Rule 10: a NEW ledger id CARRIES ITS ARC'S PREFIX, and the prefix agrees.

Its own module rather than a function on :mod:`_registry`, for the mechanical
reason :mod:`_rulings` and :mod:`_row_width` were split out before it:
``_registry`` stands at exactly 1000 lines, which is pylint's
``max-module-lines``, and this package holds a 10.00/10 floor with
``--fail-under=10``.  A predicate appended there would fail the gate that
guards the gate.

**What the ruling fixes** (developer, 2026-09-05).  ``N-`` ids came from ONE
pot that five concurrent sessions drew from, and the pot had run out of gaps:
balance's highest id was ``N-461`` with ``bank_import``'s run beginning at
``N-470``, leaving EIGHT -- and ``balance:X-br-3`` had just spent four of them
in a single step.  A prefix does not carve that pot up; it means there is no
single pot, because ``BAL-`` and ``BI-`` cannot collide by construction.

**What it makes structurally unnecessary.**  Rule 10 has carried this sentence
since the rulings lift: *where a bare id is ambiguous the citation must name
its arc -- no gate grades that; the writer does.*  It exists because a bare id
travels OUTSIDE this table, into commit messages, code comments and archived
as-built records, where the ``arc`` COLUMN does not follow it.  A prefixed id
carries its arc everywhere it goes, so for everything minted from here that
rule has nothing left to police.

**Reserved numeric RANGES were the first answer and were REJECTED** (developer,
same day), after an adversarial review re-measured their sizing: balance holds
170 live ``N-`` ids against 41 shipped steps, about 4.1 per step and a LOWER
bound since rule 5 deletes closed rows, with 88 steps still open -- so a
100-wide block covered about 24 of them.  A range also encodes the arc
implicitly, needing this module to decode ``N-847`` back to ``pay_calendar``,
and a range is a resource that runs out where a namespace is not.

**Nothing already filed is renamed**, because rule 10's own first paragraph
forbids it.  So this module grades ONLY ids whose prefix is one of the six
below or an unrecognised family, and is silent on the seven that predate the
ruling.  That silence is a CARVE-OUT, and :mod:`test_ident_prefix` mutates it
in its own direction rather than trusting it.

**The arc is now written TWICE per row** -- the column and the prefix -- which
is rule 14's tell, and this module is the reconciler that makes the pair
gradeable rather than merely conventional.  The duplication is justified by the
one thing the column cannot do: leave the table.
"""
from __future__ import annotations

import re

from _registry import ledger_rows

#: The prefix each arc MINTS new ``ledger.md`` ids with (developer, 2026-09-05).
#:
#: Each arc's numbering CONTINUES its own ``N-`` run rather than restarting at
#: 1: ``BAL-462`` follows ``N-461``.  Restarting would put ``BAL-1`` beside the
#: live ``N-14``, ``N-18`` and ``N-23``, and a reader would have to know the two
#: runs are unrelated.
ARC_PREFIXES = {
    "balance": "BAL",
    "bank_import": "BI",
    "pay_calendar": "PC",
    "recurrence": "REC",
    "salary": "SAL",
    "credit_card": "CC",
}

#: The HYPHENATED id families that predate the ruling, as a CENSUS.
#:
#: Taken over every ``ledger.md`` row on ``dev`` at ``b26de480``, 2026-09-05:
#: ``N-`` 223 rows, ``F-`` 3, ``FU-`` 2.  The other four families in the corpus
#: -- ``P`` 32, ``D`` 27, ``E`` 1, ``X`` 1 -- carry no hyphen and so never
#: reach :data:`PREFIXED_ID_RX` at all.
#:
#: **Enumerated rather than spelled as "everything except an arc prefix"**: a
#: set defined by SUBTRACTION claims members nobody censused, and this project
#: has already paid for one.  The cost of stating it is that a family added
#: later must be added here; the benefit is that ``BLA-462``, a typo of
#: ``BAL``, is REPORTED instead of silently skipped as some seventh family.
LEGACY_HYPHENATED_FAMILIES = frozenset({"N", "F", "FU"})

#: The PREFIX an id leads with, anchored at the start and nothing more.
#:
#: **It deliberately does not match the number.**  The first draft used
#: ``fullmatch`` against ``<UPPERCASE>-<digits>`` and argued that a SUFFIXED id
#: should be skipped, since suffixes are this project's decomposition spelling
#: (rule 2).  A mutation test refuted the argument: under that pattern
#: ``BI-477a`` filed in ``balance`` was silent, and loosening ``fullmatch`` to
#: ``search`` killed no control -- an unkilled mutation naming a real hole
#: rather than a slack test.  The rule is about the PREFIX, so the predicate
#: reads the prefix and stops.
#:
#: ``bare_ident`` is the id cell with its provenance annotation stripped, so
#: the anchor is not protecting against prose -- this predicate never sees any.
#: What it does exclude is the un-hyphenated families (``P76``, ``D52``,
#: ``E2``, ``X5``), which reach no arm here at all.
PREFIXED_ID_RX = re.compile(r"^(?P<prefix>[A-Z]+)-")


def arc_of_prefix() -> dict[str, str]:
    """Return the prefix-to-arc map, inverted from :data:`ARC_PREFIXES`.

    Inverted rather than stored, so widening or renaming a prefix cannot leave
    a second copy behind -- rule 14 on this module's own scale.  That two arcs
    sharing a prefix would silently lose one here is what
    ``test_no_two_arcs_share_a_prefix`` exists to refuse.

    Returns:
        Prefix to the arc that mints it.
    """
    return {prefix: arc for arc, prefix in ARC_PREFIXES.items()}


def ledger_id_prefix_violations() -> list[str]:
    """Rule 10: a prefixed ledger id names the arc of its own row.

    Ids in :data:`LEGACY_HYPHENATED_FAMILIES`, and ids carrying no hyphen at
    all, predate the 2026-09-05 ruling and are NOT graded -- rule 10 forbids
    renaming them.

    Returns:
        One message per row whose prefix names a DIFFERENT arc than its own
        column, plus one per row whose prefix belongs to no known family.
    """
    owner = arc_of_prefix()
    problems = []
    for row in ledger_rows():
        match = PREFIXED_ID_RX.match(row.bare_ident)
        if match is None:
            continue
        prefix = match["prefix"]
        if prefix in LEGACY_HYPHENATED_FAMILIES:
            continue
        arc = owner.get(prefix)
        if arc is None:
            problems.append(
                f"{row.key}: {prefix!r} is neither an arc's prefix nor one of "
                f"the families that predate the ruling "
                f"({', '.join(sorted(LEGACY_HYPHENATED_FAMILIES))}) "
                "(conventions.md rule 10). If it is a typo, fix it; if it is a "
                "new family, it is a developer ruling and belongs in the rule."
            )
            continue
        if arc == row.arc:
            continue
        problems.append(
            f"{row.key}: the prefix {prefix!r} is {arc}'s, and this row's arc "
            f"is {row.arc!r} (conventions.md rule 10). A new id carries its "
            f"OWN arc's prefix -- {row.arc} mints {ARC_PREFIXES[row.arc]!r} -- "
            "and ids are reserved through the coordinator session when several "
            "are running, because a grep cannot see a new branch."
        )
    return problems
