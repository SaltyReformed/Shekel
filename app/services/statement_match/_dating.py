"""What the MATCH pane says about WHEN, for one candidate row against one bank line.

Plan step ``bank_import:X-gz``, ruling **bank_import:R-BI9**, finding
**BI-498**.  **The pane printed ONE date per row, unlabelled, and it was the
wrong one.**  ``settled_on`` is the day the app records the money as having
left, and a balance true-up stamps the day the balance was asserted FOR onto
every open purchase it settles -- so on the developer's own Checking 66 of 110
envelope purchases read "2026-08-18" whatever day they were bought (April 12
to August 17).  Offered a `$47.61` BJ's fuel purchase made 07-13 against the
07-14 bank line, the pane said "2026-08-18"; the developer nearly refused a
correct match, could no longer verify any proposal by date, and stopped the
first production matching session at 22 of 172 lines.  The matcher was right
on that pairing: :attr:`~._subjects.CandidateRow.expected_window` reads an
asserted stamp as a purchase's UPPER bound and never as its floor, so the
07-14 line sat inside ``07-13 .. 08-18`` -- and only the label was wrong.

**What a reviewer verifies a proposal BY is the row's budget clock**: the day a
purchase was made, and the paycheck the row is budgeted in.  Those are the
facts that identify the row, and the bank's posted day is what they are
compared against.  So this module prints exactly those, each carrying the KIND
of day it is so no bare date is read as another kind, and one sentence
measuring the bank's posted day from that clock, which finishes the
comparison for the reviewer -- *the bank posted it 1 day after the purchase*.

**The settle day is NOT printed, whatever its basis** (developer ruling
2026-09-16, choosing the literal reading of R-BI9's *the settle stamp is not a
day to show* over printing it labelled by kind).  Whatever the app recorded on
the cash clock is what accepting REWRITES -- the pane's own intro says *ticked
rows take the bank's day* -- so it verifies nothing; and the one basis that is
a real observation (a statement already showed this money) reaches this pane
only through a released match, where the day it holds is the day of the line
the owner has just unlinked.  A settled row and an unsettled one therefore
print alike here.

**The distance is measured from the budget clock and never from the window.**
:attr:`~._subjects.CandidateRow.expected_window` folds the asserted stamp in as a
purchase's upper bound, which is right for BOUNDING a pairing and wrong for
this sentence: a bank line inside ``07-13 .. 08-18`` is "0 days outside" the
window and 1 day after the purchase, and the second is what the reviewer asked
for.  The arithmetic is :func:`~._pairing.signed_days_outside`, the matcher's
own subtraction given its sign: one function under both readers, applied to
different spans on purpose, so the two can differ only in what they measure
and never in how.

**The register is the paycheck's** (developer ruling 2026-09-16): the period
prints through :attr:`~app.services.pay_calendar.DerivedPeriod.label`, the
one accessor that names a paycheck, and a single day through
:func:`~app.utils.dates.day_label` beside it, so ``07/13`` sits against
``07/16 - 07/29`` and not against ``2026-07-16 - 2026-07-29``.

**Composed here and not in Jinja**, for the reason every labelled sentence in
this package is: the kind of a row is :class:`~._subjects.RowKind` and a
template may not branch on an enum's name, and a partition restated in a
template is a second place for it to be wrong on a screen about money.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.utils.dates import day_label

from ._offers import BankLine
from ._subjects import CandidateRow, RowKind
from ._pairing import signed_days_outside

#: The KIND of each date the pane prints, as the word printed before it.
#: Two of R-BI9's three; the third, *settled*, names no date this pane prints
#: (see the module docstring).
PURCHASED: str = "purchased"
BUDGETED: str = "budgeted"

#: What the pane says about a row it can place in no paycheck.  Unreachable
#: through the offer set -- both constructors fill the clock from a NOT NULL
#: column or decline the row -- and stated rather than left to raise, which is
#: the discipline :func:`~._offers.corrected_purchase_day` applies to its own
#: impossible case.
UNDATED: str = "the app holds no pay period for it"


@dataclass(frozen=True)
class DayFact:
    """One labelled date the pane prints for a row.

    Attributes:
        label: The KIND of day, printed before it: :data:`PURCHASED` or
            :data:`BUDGETED`.  Words and never a branch key -- the template
            prints them and decides nothing on them.
        text: The day, or the paycheck's span, in the paycheck register.
    """

    label: str
    text: str


@dataclass(frozen=True)
class RowDays:
    """Everything the pane prints about WHEN for one row against one line.

    Attributes:
        facts: The labelled dates, in the order they print: a purchase's own
            day first, then the paycheck the row is budgeted in.  A transaction
            has no purchase day; a purchase whose envelope period the calendar
            cannot name has no budgeted fact.
        gap: One sentence measuring the bank's POSTED day from the row's
            budget clock -- the purchase day for a purchase, the whole pay
            period for a transaction.
    """

    facts: "tuple[DayFact, ...]"
    gap: str


def _days(count: int) -> str:
    """Return *count* as ``"1 day"`` or ``"N days"``.

    Args:
        count: A positive number of days.

    Returns:
        The phrase.
    """
    return f"{count} day" if count == 1 else f"{count} days"


def _gap(row: CandidateRow, posted_on: date) -> str:
    """Return the sentence measuring *posted_on* from *row*'s budget clock.

    Args:
        row: The candidate.
        posted_on: The day the bank posted the line.

    Returns:
        The sentence.
    """
    first, last = row.expected_on, row.expected_through
    if first is None or last is None:
        return UNDATED
    clock = "the purchase" if row.kind is RowKind.PURCHASE else "that pay period"
    distance = signed_days_outside((first, last), posted_on)
    if distance == 0:
        inside = (
            "on the purchase day" if row.kind is RowKind.PURCHASE
            else "inside that pay period"
        )
        return f"the bank posted it {inside}"
    side = "after" if distance > 0 else "before"
    return f"the bank posted it {_days(abs(distance))} {side} {clock}"


def row_days(row: CandidateRow, line: BankLine) -> RowDays:
    """Return what the pane prints about WHEN for *row* against *line*.

    Args:
        row: The candidate row, as the offer set built it.
        line: The bank line the card is about.

    Returns:
        Its :class:`RowDays`.  The purchase day prints only for a purchase;
        the paycheck prints for every row whose period the calendar named,
        which is every row the offer set can produce.
    """
    facts: "list[DayFact]" = []
    if row.kind is RowKind.PURCHASE and row.purchased_on is not None:
        facts.append(DayFact(label=PURCHASED, text=day_label(row.purchased_on)))
    if row.period is not None:
        facts.append(DayFact(label=BUDGETED, text=row.period.label))
    return RowDays(facts=tuple(facts), gap=_gap(row, line.posted_on))
