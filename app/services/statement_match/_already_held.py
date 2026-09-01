"""What the books ALREADY HOLD for one deposit's pay period.

Plan step ``bank_import:X-gj-2b``.  Split out of :mod:`._reads`, whose subject
is *what the review screen shows about this pass*; this one answers a narrower
question that TWO pipelines now ask -- *does this period already hold income no
bank line explains, which recording this line would count twice*.

**The split is what the 1,000-line ceiling on ``_reads`` was measuring.**  It
had one caller while only the INCOME door asked it.  Ruling **R-II** routes a
container-answered merchant credit into the PURCHASE pipeline
(:func:`~._verdict.ruled`), which must ask the same question of the same rows
-- and a predicate reached from two pipelines through a method on one of their
read models is the seam that ceiling exists to surface.

Services-boundary discipline: plain data in, a frozen dataclass out, no Flask
import, no query.  Every fact it needs arrives as an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ._offers import BankLine, CandidateRow


@dataclass(frozen=True)
class IncomeAlreadyRecorded:
    """The unexplained INCOME the books already hold for one deposit's period.

    Ruling **bank_import:R-GW**, added after this step's own adversarial review measured
    what the card was really protected by.  **Recording a deposit the books
    already hold is the only way this door can double-count money**, and the
    per-line safeguard the card was written around -- the pass's own near-miss
    sentence (:meth:`ReviewSet.search_gap_for`) -- fires only where some TIER
    admitted a candidate and declined it.  Measured on the developer's own
    data 2026-08-27: it fires on **4 of 16** recordable inflows, and the three
    it misses hardest are `$2,612.98`, `$2,612.97` and `$2,612.97` payroll
    deposits -- **`$7,838.92`** -- each sitting in a pay period whose books
    hold a `$2,473.38` salary row nothing explains.  Those rendered a bare
    one-click tick, with only a card-level paragraph between the owner and a
    duplicate; *a warning paragraph is not a door* is what ruling **R-GJ**
    measured `$7,412.94` going through.

    **It is a FACT and not a candidate**, which is the distinction ruling
    **R-GD**'s third amendment turns on: that amendment withdrew the reviewed
    line's candidate LIST because no bound made one anything but noise -- 0 of
    18 inspected correct.  This names no candidate and scores nothing.  It
    answers *does your budget already hold income in this pay period that no
    bank line explains*, which is a question about the PERIOD, and it is the
    question whose answer decides whether recording this is a duplicate.

    **The one narrowing is a PROOF, not a threshold**, and it is the same
    argument ruling **bank_import:R-GW** rests on: a deposit SMALLER than the smallest
    unexplained income row in its period cannot be any subset of them, because
    every one of them is positive and already exceeds it.  So the five
    dividends of `$0.12`-`$0.22` and the three card refunds of `$11.73`-
    `$28.29` -- the eight lines this whole step exists for -- say nothing,
    while every payroll deposit does.  Measured on the developer's own data
    2026-08-27: **8 of 16** recordable inflows warn, against 4 of 16 for
    ``search_gap_for`` alone, and the three payroll deposits worth `$7,838.92`
    that had NO per-line signal now have one.

    **The obvious alternative tightening is refused**: warn only where the
    rows could SUM to this line is measured false on the shape it exists for --
    the 2026-03-26 payroll deposit is `$2,573.42` and its period's two rows
    come to `$2,573.38`, **four cents short**, which is finding **N-239**
    exactly.  A bound that misses the case it was built for is the tolerance
    this arc refuses; a bound that only drops what provably cannot match is
    not one.

    **THE SET IS WIDER THAN THE NAME SINCE RULING bank_import:R-II, AND THE
    NAME IS A DEBT plan step ``bank_import:X-gj-2b-3`` OWNS.**  The filter is
    ``cash_amount > 0`` over :attr:`~._reads.ReviewSet.unmatched_rows`, which
    holds PURCHASE rows beside transaction rows -- and
    :func:`~._candidates.purchase_candidate` sets ``cash_amount`` to
    ``-entry.amount``, so a stored REFUND is a positive-cash row here.  That is
    the right SET: the question is *could this money already be in the books*,
    and a refund the books already hold is money that already arrived.  It is
    the wrong WORD, so no sentence composed here says *income* -- see
    :meth:`why_it_could_double_count`.  Renaming the class, the
    :meth:`~._reads.ReviewSet.income_already_recorded_in` method and the
    ``income_already_held`` field the queue, the cards and two templates carry
    is 70 references and belongs with the rest of that leaf's label work.

    Attributes:
        rows: The unexplained ARRIVING rows whose pay period covers the day the
            bank credited this line, in the order the offer set holds them.
        total: What they come to, POSITIVE, so the screen states the figure
            without arithmetic in a template.
    """

    rows: "tuple[CandidateRow, ...]"
    total: Decimal

    @property
    def why_it_could_double_count(self) -> str:
        """Return the clause both withholding sentences are built from.

        **ONE composition, because two spellings of one rule are two things
        that can come to disagree** (plan step ``bank_import:X-gj-2b``, after
        that step's own adversarial review).  The receipt the INCOME door
        writes (:func:`~._filing._inflow_filings`) and the verdict the PURCHASE
        pipeline writes (:func:`~._verdict.ruled`) are about the same fact
        about the same period, and :mod:`._filing` states the rule for exactly
        this in as many words: *the sentence this receipt reports and the
        sentence the review screen prints beside the same line are the same
        value rather than two spellings of one rule*.  The second spelling
        arrived with the refund half, and it had already drifted -- it printed
        the figure as a bare ``Decimal``.

        **It does not say *income*, and that is not a wording preference.**
        The rows it totals are every ARRIVING row the books hold and no line
        explains, which since ruling **bank_import:R-II** includes a stored
        refund -- a negative purchase, whose cash is positive.  The class is
        still called :class:`IncomeAlreadyRecorded` because renaming it reaches
        70 references across two templates; the SENTENCE is what the owner
        reads, so the sentence is what has to be true first.

        **The FIGURE carries a currency symbol and separators**, which is what
        every other money sentence this package composes does
        (:func:`~._batch._created_summary`, :mod:`._gaps`).  Both sides read
        ``2473.38`` where the card beside them read ``$2,473.38``, because a
        template's ``money()`` filter never reaches a string a service already
        composed.

        Returns:
            The clause, with no leading capital and no trailing stop, so each
            caller sets it in its own sentence.
        """
        return (
            f"the pay period it falls in already holds "
            f"${self.total:,.2f} your records say arrived and no bank line "
            f"explains, so recording it automatically could count the same "
            f"money twice"
        )



def income_already_recorded(
    unmatched_rows, line: BankLine,
) -> "IncomeAlreadyRecorded | None":
    """Return what the books already hold for *line*'s period, or ``None``.

    **The ONE statement of the double-count safeguard, and it has TWO callers
    since plan step ``bank_import:X-gj-2b``.**  It was a method body reached
    only by :meth:`ReviewSet.income_already_recorded_in`, which serves the
    lines the INCOME pipeline owns.  That step routes a container-answered
    merchant credit into the PURCHASE pipeline instead, and those lines are
    ruled by :func:`~._verdict.ruled` -- so a hazard this package added a
    control for was live for a class the very next change routed past it.
    Extracted rather than re-asked, so the two pipelines cannot come to
    disagree about what the books already hold.

    **The period is tested by the row's own SPAN** (``expected_on`` ..
    ``expected_through``) rather than by a pay-period id the row does not
    publish.  The span IS the period.

    Args:
        unmatched_rows: The candidate rows no bank line explains.
        line: The inflow being considered.

    Returns:
        The :class:`IncomeAlreadyRecorded`, or ``None`` when this period's
        books hold nothing that could be the same money.
    """
    day = line.posted_on
    rows = tuple(
        row for row in unmatched_rows
        if row.cash_amount > 0
        and row.expected_on <= day <= row.expected_through
    )
    # **A deposit smaller than the SMALLEST of them cannot be any subset of
    # them**, every one being positive -- so there is nothing for the owner
    # to check and a sentence here would be the warning-on-every-row shape
    # this package measures money going through.  A PROOF rather than a
    # bound: it drops only what cannot match, at any tolerance.
    if not rows or line.amount < min(row.cash_amount for row in rows):
        return None
    return IncomeAlreadyRecorded(
        rows=rows,
        total=sum((row.cash_amount for row in rows), Decimal("0.00")),
    )
