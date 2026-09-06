"""
Shekel Budget App -- plan gate: how WIDE the ledger's rows have grown.

Rule 4's corpus signal, split out of :mod:`_registry` at plan step
``balance:X-au-g-2b`` when adding it took that module to 1,083 lines against
pylint's 1,000 ceiling.  **Split rather than trimmed, deliberately**: this
project has already ruled that "three lines of headroom is not a design, and
the structural answer is a package with one private leaf per verb"
(``transaction_service``), and shaving an argument to fit a cap is how a
registry loses the reason for its own rules.  Row WIDTH is a concern of its
own -- ``_registry`` answers what the ledger CONTAINS, this answers how big
its entries have become -- so the cut is by concern, the way ``_order`` and
``_rulings`` already are.

Imports :mod:`_registry` and nothing else here imports this, so the arrow runs
one way.

**The ledger PATH is read as ``_registry.LEDGER`` at call time rather than
bound at import, and the stale-count control is what caught the difference.**
The staging fixture re-points ``registry.LEDGER`` at a mutated copy; a
``from _registry import LEDGER`` snapshot keeps the original, so every
control that plants a defect would have graded the REAL file and passed.
A module-qualified read is what lets a monkeypatched path reach a leaf that
did not define it.
"""
from __future__ import annotations

import re

import _registry
from _registry import LEDGER_ROW_CAP, ledger_rows

#: The width at which a ``ledger.md`` row counts as CROWDING the row cap.
#:
#: Half the cap, which is where the corpus arm drew "crowding" while it was a
#: gate.  A row here is not over the cap, but it is no longer an ordinary index
#: entry either.
LEDGER_CROWDING_WIDTH = LEDGER_ROW_CAP // 2


def crowded_ledger_rows() -> list[str]:
    """Rule 4's corpus signal: the ``ledger.md`` rows crowding the row cap.

    A row at or over :data:`LEDGER_CROWDING_WIDTH` -- half the cap -- is not
    over the cap, but it is no longer an ordinary index entry.  How many there
    are says whether :data:`LEDGER_ROW_CAP` is still catching outliers or has
    become a ceiling the whole table is growing toward.

    **REPORTED, never gated, by developer ruling of 2026-09-01 -- and two
    instruments were measured wrong before that ruling was asked for.**

    * A MEDIAN, which is what the arm used.  Archiving pushes it UP whenever the
      archived rows are narrower than the middle, so a step whose entire
      registry edit was CLOSING three finished findings turned it red.
    * A COUNT against a fixed budget, which is monotone under archiving --
      removing rows can only remove matches -- but does not grade the property
      at all: **160 rows of 1,999 characters each would have passed it**, with
      zero per-row violations, while being a table made entirely of
      specifications.  At the measured growth of ~2 crowding rows a day, a
      budget with 19% headroom binds inside a fortnight.

    **The two requirements are incompatible, which is why there is no third
    instrument here.**  "Monotone under rule 5" and "relative to the corpus"
    cannot both hold of one statistic: a relative measure is by definition moved
    by what LEAVES the corpus, and rule 5's whole job is to make things leave.
    The failing arm was the wrong SHAPE, not the wrong calibration.

    What replaces it is the precedent this file already runs on.  The line caps
    on ``ledger.md`` and ``steps.md`` were dropped on 2026-08-25 for the same
    class of reason -- *"the cap did not force the ledger to shrink, it forced a
    measured defect out of the registry that exists to hold it"* -- and what
    took over was the backlog being STATED in the file, with
    :func:`stated_arc_counts_violation` grading only that the statement is true.
    :func:`stated_crowding_violation` is that pattern for row width.  The
    failures about SIZE remain exactly two: :func:`ledger_row_cap_violations`
    per row, and :func:`ledger_runaway_violation` as the backstop.

    Returned as keys rather than as a bare count, so a reader who wants to act
    on the number is handed the rows rather than left to find them.

    Returns:
        The ``row.key`` of every row at or over :data:`LEDGER_CROWDING_WIDTH`,
        in table order.
    """
    return [
        row.key for row in ledger_rows()
        if row.width >= LEDGER_CROWDING_WIDTH
    ]


def stated_crowding_violation() -> "str | None":
    """Rule 3, applied to the row-width signal the corpus arm used to gate on.

    ``ledger.md`` states how many of its rows crowd the row cap; this grades
    that the file is telling the truth about itself, and nothing else.  The
    number may be any size and may rise, and no gate will refuse the edit --
    which is the whole point of the 2026-09-01 ruling.  What it may not do is go
    STALE, because a figure nobody grades is a figure nobody can rely on, and
    this registry has already had one such figure decay unnoticed
    (:data:`LEDGER_ROW_CAP`'s p90).

    Returns:
        The message when the stated count disagrees with the table, or ``None``.
    """
    actual = len(crowded_ledger_rows())
    match = re.search(r"(\d+) of its rows crowd", _registry.LEDGER.read_text())
    if match is None:
        return (
            "ledger.md states no crowding count. Rule 4's corpus signal is "
            "REPORTED rather than gated (developer ruling 2026-09-01), so the "
            "file must carry it: add a sentence of the form "
            f"'{actual} of its rows crowd the {LEDGER_ROW_CAP}-character row "
            f"cap' (at or over {LEDGER_CROWDING_WIDTH} characters)"
        )
    if int(match.group(1)) == actual:
        return None
    return (
        f"ledger.md says {match.group(1)} of its rows crowd the row cap and "
        f"the table holds {actual}. Nothing here refuses a wide table -- the "
        "count is REPORTED rather than gated -- so the one thing that must "
        "hold is that the number is TRUE where a reader meets it "
        "(conventions.md rule 3; the argument is on crowded_ledger_rows)"
    )
