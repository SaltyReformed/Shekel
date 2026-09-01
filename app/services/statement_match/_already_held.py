"""What the books ALREADY HOLD as ARRIVING, for one bank line's pay period.

Plan step ``bank_import:X-gj-2b``.  Split out of :mod:`._reads`, whose subject
is *what the review screen shows about this pass*; this one answers a narrower
question that TWO pipelines now ask -- *does this period already hold money
arriving that no bank line explains, which recording this line would count
twice*.

**The question is about ARRIVING money and not about INCOME**, and every name
in this module said the narrower word until plan step
``bank_import:X-gj-2b-3``.  The set is filtered on ``cash_amount > 0`` over
``ReviewSet.unmatched_rows``, which holds PURCHASE rows too -- a stored refund
is a positive-cash row there since ruling **bank_import:R-II** -- so the
answer always included rows that are not income.

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
class ArrivalsAlreadyHeld:
    """Every unexplained ARRIVAL the books already hold for one line's period.

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
    answers *does your budget already hold money arriving in this pay period that no
    bank line explains*, which is a question about the PERIOD, and it is the
    question whose answer decides whether recording this is a duplicate.

    **The one narrowing is a PROOF, not a threshold**, and it is the same
    argument ruling **bank_import:R-GW** rests on: a deposit SMALLER than the smallest
    unexplained arriving row in its period cannot be any subset of them, because
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
    the wrong WORD, and the NAME is what plan step ``bank_import:X-gj-2b-3``
    changed: this class was ``IncomeAlreadyRecorded``, the method was
    ``ReviewSet.income_already_recorded_in``, and the field the queue, the
    cards and two templates carry was ``income_already_held`` -- 74 references
    across 13 files, renamed together so no half of it can go on saying
    *income* about a set that holds refunds.  The two template SENTENCES said
    it out loud (*already holds N income row(s)*) and were changed in the same
    pass; the service-composed one had already been corrected at
    ``bank_import:X-gj-2b`` (see :meth:`why_it_could_double_count`), which is
    what left the name and the words disagreeing.

    Attributes:
        rows: The unexplained ARRIVING rows whose pay period covers the day
            the bank credited this line, in the order the offer set holds them.
            **Not only income rows**: a stored REFUND is a positive-cash row
            in ``unmatched_rows`` (ruling **bank_import:R-II**), and it is
            money the books already hold arriving exactly as a salary row is.
        total: What they come to, POSITIVE -- every member's ``cash_amount``
            is, by the filter that selects them -- so the screen states the
            figure without arithmetic in a template.
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
        refund -- a negative purchase, whose cash is positive.  **The class
        was still called ``IncomeAlreadyRecorded`` when this sentence was
        written**, because renaming it reached 74 references across 13 files;
        the SENTENCE is what the owner reads, so it was made true first and
        plan step ``bank_import:X-gj-2b-3`` renamed the rest to match.

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



def arrivals_already_held(
    unmatched_rows, line: BankLine,
) -> "ArrivalsAlreadyHeld | None":
    """Return what the books already hold for *line*'s period, or ``None``.

    **The ONE statement of the double-count safeguard, and it has TWO callers
    since plan step ``bank_import:X-gj-2b``.**  It was a method body reached
    only by :meth:`ReviewSet.arrivals_already_held_in`, which serves the
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
        The :class:`ArrivalsAlreadyHeld`, or ``None`` when this period's
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
    return ArrivalsAlreadyHeld(
        rows=rows,
        total=sum((row.cash_amount for row in rows), Decimal("0.00")),
    )
