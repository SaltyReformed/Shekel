"""What this pass did NOT look at, and why it cannot conclude about a line.

Plan step ``bank_import:X-gf-3a``.  Two values with one subject -- the LIMITS
of one pass over one account -- split out of :mod:`._reads` so that the module
which answers *what does the review screen show* is not also the module that
answers *what did this pass fail to look at*.

**The split is what makes the rule verdict possible at all.**
:mod:`._verdict` has to ask why the pass would not conclude about a line, and
asking :class:`~._reads.ReviewSet` would be a cycle: :mod:`._filing` imports
:mod:`._reads`, so :mod:`._reads` cannot import the module that reads its own
verdict.  Housing the question here rather than in :mod:`._verdict` is the
other half of the same discipline: every live caller of :func:`search_gap`
except one is about a line no rule reaches -- the queue's INFLOW rows, the
hand-build list -- so a module named for what a RULE comes to would have been two
subjects wearing one name.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
it holds arrives from the pass that measured it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ._offers import BankLine
from ._pairing import DAY_WINDOW


@dataclass(frozen=True)
class ReviewBounds:
    """What the review DID NOT look at, and why.

    **A screen that lists what it could explain and says nothing about what it
    could not reads as a clean sweep.**  These facts are one subject -- the
    limits of this pass -- and they travel together so a caller cannot render
    the proposals while forgetting the caveat.

    Attributes:
        calendar_opens: The first day the owner's pay calendar covers, or
            ``None`` for an owner with no periods at all.
        before_calendar_count: How many recorded lines fall before it, which
            nothing can ever match: there are no rows to match them to.  A
            COUNT and a last day rather than the rows themselves -- they are
            not work, they are the statement being older than the budget.
            Measured at 130 of 361 on the developer's own export.
        before_calendar_last_day: The latest of those days, or ``None``.
        crowded_days: Days the GROUP search refused to look at, as it
            reports them (:attr:`~._propose.ProposedMatches.crowded_days`).
        unpriceable_count: How many of the account's rows the amount model
            could not price, so they could not be offered
            (:class:`~._offers.Candidates`).
        impossible_day_count: How many unexplained OUTFLOWS the bank dates as
            MADE after it POSTED them, so no day exists that a purchase could
            be made on (finding **N-325**, developer ruling 2026-08-19).
            ``entry_service.create_entry`` refuses a purchase whose money left
            before it was spent, correctly, so offering these a destination
            chooser renders a control whose submission can never succeed --
            the *chooser whose submission always fails* shape this package has
            now named four times.  **Reported rather than repaired**: the
            other remedy was to clamp the purchase day to the earlier of the
            two, which decides which day the app believes when the bank
            contradicts itself, and ruling **R-FW** refused exactly that
            substitution one clock over.  0 of the developer's own 361
            recorded lines are this shape; the OFX adapter's own measurement
            found 2 of 361, so a second source makes it live.

    **The near tier's bound is NOT here, and that is plan step
    ``bank_import:X-f6d-3``'s one deliberate exception to the paragraph above.**
    It was ``undecided_near_count``, and a count in this panel names no line:
    the owner was told that somewhere among a hundred lines one had a near
    candidate the page would not choose, with no way to find it.  A bound is
    only a bound if it can be acted on, so it moved onto the LINE
    (:attr:`ReviewSet.declined_lines`), where the act it should prompt is
    already offered -- and the panel keeps the four limits that genuinely
    belong to the PASS rather than to any one line.
    """

    calendar_opens: "date | None"
    before_calendar_count: int
    before_calendar_last_day: "date | None"
    crowded_days: "tuple[date, ...]"
    unpriceable_count: int
    impossible_day_count: int = 0

    @property
    def any_limit(self) -> bool:
        """Return whether this pass left anything unexamined.

        The one question the QUEUE's template asks, answered here rather than
        as four ``or``-ed truth tests in a Jinja condition -- where a fifth
        limit added later would silently not appear.
        """
        return bool(
            self.before_calendar_count
            or self.crowded_days
            or self.unpriceable_count
            or self.impossible_day_count
        )

    @property
    def any_pick_list_limit(self) -> bool:
        """Return whether anything is missing from the WORKBENCH's two lists.

        Plan step ``bank_import:X-gf-3b``, ruling **bank_import:R-HC**.  The
        hand-build form is a surface of its own now, and it renders two lists
        that are each SHORTER than the fact they are captioned as: so it owes
        the same *no silent caps* sentence :attr:`any_limit` owes the queue.

        **Two of the four limits, and the partition is decided HERE rather
        than by the template picking two** -- which would be this package's own
        *a template restating a partition is a second place for it to be
        wrong* stated a sixth time.  Each of the four was traced to the list it
        does or does not bound:

        * :attr:`before_calendar_count` bounds the LINE list.
          ``_reads._split_at_calendar_open`` removes those lines before
          ``unmatched`` is derived, so 130 of the developer's own 378 are
          absent from it (re-counted 2026-08-28; a first version of this line
          said 361, which is one export behind what
          :mod:`._leftovers` and the review body both already say).
        * :attr:`unpriceable_count` bounds the ROW list.
          :class:`~._offers.Candidates` keeps unpriceable ids OUT of ``rows``,
          so they never reach ``unmatched_rows`` either.
        * :attr:`impossible_day_count` bounds NEITHER.
          ``_leftovers._creatable_lines`` drops those lines from ``creatable``
          only; they stay in ``unmatched`` and the line list renders them, so
          naming them here would claim an absence that is not one.
        * :attr:`crowded_days` bounds NEITHER, and it is the one worth stating
          why.  A crowded day means the GROUP search did not run, which leaves
          MORE lines unexplained rather than fewer -- every one of them in the
          line list.  The reason such a line is still there is already printed
          against the line itself (:func:`search_gap`), where it can be acted
          on, which is the same argument plan step ``bank_import:X-f6d-3``
          made when it moved the near tier's bound out of the panel.
        """
        return bool(self.before_calendar_count or self.unpriceable_count)


def search_gap(
    line: BankLine,
    declined_lines: "dict[int, str]",
    crowded_days: "tuple[date, ...]",
    unpriceable_count: int,
) -> "str | None":
    """Return why this pass cannot say *line* has no counterpart, or ``None``.

    Plan step ``bank_import:X-ge``, developer ruling 2026-08-26, corrected at
    ``X-ge-1``, moved here from :class:`~._reads.ReviewSet` at ``X-gf-3a``.
    :meth:`~._reads.ReviewSet.search_gap_for` is the screen's spelling of it
    and delegates here.

    **It READS what the search reports and derives nothing**, which is the
    whole of the correction ``X-ge-1`` made.  A first version enumerated the
    bounds :class:`ReviewBounds` and the near tier PUBLISH, and called
    that enumeration complete; an adversarial review measured it false twice
    over, because the matcher applies more bounds than it published.
    Re-deriving them here would have been a third spelling of
    :data:`~._near.NEAR_MISS_BOUND` and :data:`~._pairing.DAY_WINDOW` outside
    the modules that own them -- finding **N-322** exactly, which
    :mod:`._pairing`'s own header predicts in as many words.  So each tier
    reports its own refusals now (:attr:`~._propose.ProposedMatches
    .declined_lines`) and this joins them to the two bounds that belong to the
    PASS rather than to any line.

    **What that makes true:** a tier added later must put its refusals in
    ``declined_lines`` or they are invisible, which is the same rule the search
    already keeps for its crowded days -- rather than this function having to
    be taught about it.

    **Why the answer is per LINE and not a count in a panel**: ruling
    **R-GD**'s third amendment withdrew the reviewed line's candidate LIST
    because no bound made one anything but noise -- 0 of 18 inspected correct
    -- and moved what survives onto the line itself, which is the ground plan
    step ``bank_import:X-f6d-3`` acted on and ``X-gf-3a`` extended to the rule
    verdict beside it.  A bound reported in a panel names no line and cannot be
    acted on.

    The three sources, in the order a reader should hear them:

    * what a TIER declined about this line, in that tier's own words: a near
      candidate it admitted and would not choose between (the
      `$356.61`-for-one-`$178.29` shape, finding **N-335**), one it refused for
      want of the merchant in the row's label, one it refused for the day
      window, and an EXACT candidate the window refused;
    * a CROWDED day the GROUP search skipped, measured within
      :data:`~._pairing.DAY_WINDOW` of the line because that is the window
      :func:`~._propose._groups` pairs a line to a bucket across;
    * a row the amount model could not PRICE at all.  It is account-wide and so
      is this refusal: an unpriced row is absent from the candidate set
      entirely, so there is no line it can be said not to match.

    **Measured on the developer's own 378 recorded lines (2026-08-26):** the
    last two are ZERO, and the first touches 12 of the 80 lines a standing rule
    would file -- `$391.77` -- one of which is his own `Apple Music` row
    sitting one day past the window from an `Apple` line the door would
    otherwise have recorded a second time.

    Args:
        line: The bank line, which must be one this pass considered.
            **THREE surfaces take it off three different lists**, and the claim
            that "every caller takes it off ``creatable``" was already false
            when it was written: the queue's OUTFLOW rows read it off
            :attr:`~._reads.ReviewSet.creatable`, the hand-build form off
            :attr:`~._reads.ReviewSet.unmatched` (which is the only one an
            inflow used to reach), and since ruling **bank_import:R-GW** the
            queue's INFLOW rows off :attr:`~._reads.ReviewSet.recordable_inflows`.
            What every caller does share is that the line was in THIS pass,
            which is what makes *declined_lines* answerable for it.
        declined_lines: What each tier declined about a line, by line id
            (:attr:`~._propose.ProposedMatches.declined_lines`).
        crowded_days: The days the GROUP search refused to look at, as it
            reports them (:attr:`~._propose.ProposedMatches.crowded_days`).
        unpriceable_count: How many of the account's rows the amount model
            could not price (:attr:`~._offers.Candidates.unpriceable_ids`).

    Returns:
        One sentence naming the gap, for the receipt that has to say what it
        withheld and for the screen that has to say why a line is still there;
        ``None`` when this pass searched exhaustively for a counterpart to
        *line* and found none.
    """
    declined = declined_lines.get(line.line_id)
    if declined is not None:
        return declined
    crowded = [
        day for day in crowded_days
        if abs((day - line.posted_on).days) <= DAY_WINDOW
    ]
    if crowded:
        return (
            f"{crowded[0]} held too many rows for the app to search them "
            f"for a group that adds up to this line"
        )
    if unpriceable_count:
        return (
            f"{unpriceable_count} row(s) on this account could not be priced, "
            f"so the app could not compare them against this line"
        )
    return None
