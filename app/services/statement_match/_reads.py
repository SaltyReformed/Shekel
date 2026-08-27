"""What the review screen shows: unmatched lines, proposals, and what agrees.

Read-only, and separate from :mod:`._accept` for the reason every package here
splits that way: the write door and the reader answer different questions, and
a reader living inside the door is a reader nobody can call without one.

**It reports three things a bound would otherwise hide**, because a screen that
lists what it could explain and says nothing about what it could not reads as
a clean sweep:

* lines that predate the owner's pay calendar, which nothing can ever match --
  130 of the developer's own 361 lines, and listing them beside genuine
  failures would bury the ones worth acting on;
* days too crowded to search for groups, as the SEARCH reports them
  (:attr:`~._propose.ProposedMatches.crowded_days`);
* matches whose rows no longer carry the day the bank stated, which is what a
  later hand edit produces and what makes a match re-reviewable rather than
  quietly stale.

Services-boundary discipline: reads only, plain data in, frozen dataclasses
out, no Flask import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatchMember

from ._accepted_view import AcceptedGroup, accepted_groups
from ._candidates import (
    act_still_names_a_row,
    matched_subjects,
    unmatched_destinations,
    unmatched_rows,
)
from ._offers import (
    BankLine,
    CandidateRow,
    MatchProposal,
)
from ._bars import ParkedLine
from ._leftovers import CreatableLine, RecordableInflow, leftovers
from ._pairing import DAY_WINDOW
from ._propose import propose
from ._scope import ReviewScope
from ._section import MerchantSection


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

        The one question the template asks, answered here rather than as four
        ``or``-ed truth tests in a Jinja condition -- where a fifth limit
        added later would silently not appear.
        """
        return bool(
            self.before_calendar_count
            or self.crowded_days
            or self.unpriceable_count
            or self.impossible_day_count
        )


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

    Attributes:
        rows: The unexplained income rows whose pay period covers the day the
            bank credited this deposit, in the order the offer set holds them.
        total: What they come to, POSITIVE, so the screen states the figure
            without arithmetic in a template.
    """

    rows: "tuple[CandidateRow, ...]"
    total: Decimal


@dataclass(frozen=True)
class ReviewSet:  # pylint: disable=too-many-instance-attributes
    """Everything the review screen needs, in one value.

    Pylint: too-many-instance-attributes (9/7) -- **nine because the screen
    renders nine distinct things**, not because the value wants splitting.
    Eight are cards the owner reads and acts in; the ninth is
    :attr:`declined_lines`, which annotates two of them.

    The obvious way to satisfy the limit is to fold ``declined_lines``
    back into :attr:`bounds`, where it lived until plan step
    ``bank_import:X-f6d-3`` -- and that is exactly what the step measured to be
    wrong, because a bound reported in a panel names no line and cannot be
    acted on.  The other obvious way is to fold :attr:`parked` back into
    :attr:`creatable` and let a Jinja condition withhold the control, and that
    is what ruling **R-GJ** exists because of: a sentence saying *nothing here
    records it* sat over a working select for as long as those two were one
    list.  ``AcceptedMatch`` carries the same disable for the same reason, and
    its docstring says what dropping a field to meet a limit costs: the receipt
    said *"Nothing moved."* over a rewritten figure.

    Attributes:
        proposals: What the app believes goes with what, best first.
        unmatched: Bank lines inside the pay calendar that no proposal
            explains, ascending by day.
        unmatched_rows: The app's OWN rows that no proposal explains, over the
            span the recorded statements cover -- ruling **R-FP**'s other side,
            and the more valuable half for a budget: a row the bank never
            showed *and would have shown separately* is a payment the records
            claim happened and the bank did not make.  **That qualifier is
            load-bearing and was missing until plan step ``bank_import:X-gc``**;
            :attr:`~._offers.CandidateRow.not_shown_alone` is where the screen
            withdraws the claim for a row whose money the bank accounts for
            through some other row, and the membership of this list is
            deliberately unchanged by it -- it is also the hand-build form's
            row-picker, and ruling **R-GJ** leaves the group match as a parked
            card payment's only arm.  They are
            :class:`~._offers.CandidateRow` values rather than a type of their
            own; a second record carrying the same five fields was reported by
            pylint's cross-file ``duplicate-code`` and was exactly rule 13's
            speculative shape.
        accepted: The matches already accepted, newest first.
        creatable: The unmatched OUTFLOW lines, each with the budget lines it
            could become a purchase against (:class:`CreatableLine`).  A SUBSET
            of ``unmatched`` rather than a partition of it, and deliberately:
            the same line is offered to the hand-build form as something to
            GROUP and to the create door as something to RECORD, because those
            are different acts on the same fact and the owner is the one who
            knows which it is.  Inflows are absent -- a purchase is an expense
            (``ck_transaction_entries_positive_amount``), so a deposit or a
            card refund is not one; since ruling **bank_import:R-GW** it has a door of its
            own instead (:attr:`recordable_inflows`), and until then it had
            none at all.  **A line that
            ruling R-GJ bars is not here** -- it is in :attr:`parked` -- so this
            list is exactly the lines a create control may be rendered for,
            and the screen cannot render one for a line the door would refuse.
        parked: The unmatched OUTFLOW lines that may NOT become purchases, with
            the reason each may not (:class:`~._bars.ParkedLine`, ruling
            **R-GJ**).  Its two arms are a merchant the owner answered *never a
            purchase* and a merchant a source files as a payment to a credit
            card that they have not answered for at all.  They are still in
            ``unmatched``, so the group-match arm the ruling leaves open is
            reached exactly as it was.
        recordable_inflows: The unmatched INFLOW lines, each with the period
            that would hold it (:class:`RecordableInflow`, ruling **bank_import:R-GW**).
            The mirror of ``creatable`` on the direction that had no door at
            all until plan step ``bank_import:X-gf-1``: an inflow is not a
            purchase, so the create arm refuses one, and a match needs an app
            row on the other side -- which left `$58.87` of the developer's own
            deposits, in eight lines, with no act the screen could offer.
            Like ``creatable`` this is a SUBSET of ``unmatched`` and not a
            partition: the same deposit is offered here as something to RECORD
            and in the hand-build form as something to MATCH, because those are
            different acts on one fact.  **Nothing is barred out of it**: ruling
            **R-GJ**'s bars are about SPENDING the budget already holds in
            another shape, and no answer a merchant control can hold says
            anything about a deposit.
        merchants: The rule control (:class:`~._section.MerchantSection`) --
            where this account's merchants go, and what the owner has already
            said.  **It counts** ``parked`` **beside** ``creatable``, because
            the parked half is parked for want of an answer and this is the
            control that gives one.
        bounds: What this pass did NOT look at (:class:`ReviewBounds`).
        declined_lines: WHAT THIS PASS CONSIDERED and would not conclude
            about, by line id, in the words of the tier that declined
            (:attr:`~._propose.ProposedMatches.declined_lines`).  It carried
            only the near tier's CONTEST until plan step
            ``bank_import:X-ge-1``; it now carries every rejection a tier makes
            after admitting the figure, because a bound a tier applies and does
            not report is one nothing can see.

            **It rides on the SET rather than in the bounds panel** (plan step
            ``bank_import:X-f6d-3``).  It was a count under *What this page did
            not look at*, which named no line -- so the owner was told that
            somewhere among a hundred lines one had a near candidate, with no
            way to find it.  The act it should prompt belongs to ONE line and
            is offered in two cards: build this one by hand, rather than record
            it a second time from the create arm, which is exactly the
            duplicate **N-335** measures.  The screen asks membership per line;
            the count is ``len`` and nothing needs it.
    """

    proposals: "tuple[MatchProposal, ...]"
    unmatched: "tuple[BankLine, ...]"
    unmatched_rows: "tuple[CandidateRow, ...]"
    accepted: "tuple[AcceptedGroup, ...]"
    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[ParkedLine, ...]"
    recordable_inflows: "tuple[RecordableInflow, ...]"
    merchants: MerchantSection
    bounds: ReviewBounds
    declined_lines: "dict[int, str]" = field(default_factory=dict)

    @property
    def placed_by_class(self) -> "dict[str, int]":
        """Return how many creatable lines each sweep CLASS would tick.

        **Counted where the sweep's own rule is**
        (:attr:`~._placement.Placement.sweep_class`) rather than as a Jinja
        expression, so a caption cannot promise a number the control does not
        deliver.  A placement that is not an act -- a rule that does not
        reach this line's pay period -- has no class and is not counted, and a
        line ruling **R-GJ** bars is not in :attr:`creatable` at all.

        The three partition, for the reason
        :attr:`~._offers.MatchProposal.review_class`'s three do: filing into an
        open budget line, raising what a closed one records, and minting one
        the account did not have are different acts with different
        consequences, and ruling **R-FZ(c)** is that the riskiest may not ride
        the same click as the safest.
        """
        counts: "dict[str, int]" = {}
        for item in self.creatable:
            group = (
                item.placement.sweep_class
                if item.placement is not None else None
            )
            if group is not None:
                counts[group] = counts.get(group, 0) + 1
        return counts

    def income_already_recorded_in(
        self, line: BankLine,
    ) -> "IncomeAlreadyRecorded | None":
        """Return what the books already hold for *line*'s period, or ``None``.

        The per-line safeguard on the deposit card
        (:class:`IncomeAlreadyRecorded`).  **A method over
        :attr:`unmatched_rows` rather than a field on
        :class:`~._leftovers.RecordableInflow`**, because those rows are
        derived AFTER the leftovers are split -- and a value built from a
        second read of them could disagree with the list the hand-build form
        renders, which is where the owner is being sent.

        **The period is tested by the row's own SPAN**, which is what a
        candidate carries (``expected_on`` .. ``expected_through``), rather
        than by a pay-period id the row does not publish.  The span IS the
        period, so the two are the same test asked of the value that has it.

        Args:
            line: A recordable inflow's bank line.

        Returns:
            The :class:`IncomeAlreadyRecorded`, or ``None`` when this period's
            books hold no unexplained income at all -- which is the state that
            makes recording safe, and the screen says nothing rather than
            saying it is fine.
        """
        day = line.posted_on
        rows = tuple(
            row for row in self.unmatched_rows
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

    def search_gap_for(self, line: BankLine) -> "str | None":
        """Return why this pass cannot say *line* has no counterpart, or ``None``.

        Plan step ``bank_import:X-ge``, developer ruling 2026-08-26, corrected
        at ``X-ge-1``.  **Membership of :attr:`creatable` is a set defined by
        SUBTRACTION** -- no proposal claimed the line -- and that is two
        different facts wearing one name: *the pass looked and there is
        nothing*, and *the pass threw the only candidate away*.  Under a human
        tick the difference costs nothing, because the person reading the
        screen is the check.  Under ruling **R-GH**'s auto-apply there is no
        person, so it has to be a fact the pass STATES rather than one a reader
        infers.

        **It READS what the search reports and derives nothing**, which is the
        whole of the correction ``X-ge-1`` made.  A first version enumerated
        the bounds :class:`ReviewBounds` and the near tier PUBLISH, and called
        that enumeration complete; an adversarial review measured it false
        twice over, because the matcher applies more bounds than it published.
        Re-deriving them here would have been a third spelling of
        :data:`~._near.NEAR_MISS_BOUND` and :data:`~._pairing.DAY_WINDOW`
        outside the modules that own them -- finding **N-322** exactly, which
        :mod:`._pairing`'s own header predicts in as many words.  So each tier
        reports its own refusals now (:attr:`~._propose.ProposedMatches
        .declined_lines`) and this joins them to the two bounds that belong to
        the PASS rather than to any line.

        **What that makes true:** a tier added later must put its refusals in
        ``declined_lines`` or they are invisible, which is the same rule the
        search already keeps for its crowded days -- rather than this function
        having to be taught about it.

        The three sources, in the order a reader should hear them:

        * what a TIER declined about this line, in that tier's own words: a
          near candidate it admitted and would not choose between (the
          `$356.61`-for-one-`$178.29` shape, finding **N-335**), one it refused
          for want of the merchant in the row's label, one it refused for the
          day window, and an EXACT candidate the window refused;
        * a CROWDED day the GROUP search skipped
          (:attr:`ReviewBounds.crowded_days`), measured within
          :data:`~._pairing.DAY_WINDOW` of the line because that is the window
          :func:`~._propose._groups` pairs a line to a bucket across;
        * a row the amount model could not PRICE at all
          (:attr:`ReviewBounds.unpriceable_count`).  It is account-wide and so
          is this refusal: an unpriced row is absent from the candidate set
          entirely, so there is no line it can be said not to match.

        **Measured on the developer's own 378 recorded lines (2026-08-26):**
        the last two are ZERO, and the first touches 12 of the 80 lines a
        standing rule would file -- `$391.77` -- one of which is his own
        `Apple Music` row sitting one day past the window from an `Apple` line
        the door would otherwise have recorded a second time.

        Args:
            line: The bank line, which must be one this pass considered.
                **THREE surfaces take it off three different lists**, and the
                claim that "every caller takes it off :attr:`creatable`" was
                already false when it was written: the create card reads it off
                :attr:`creatable`, the hand-build form off :attr:`unmatched`
                (which is the only one an inflow used to reach), and since
                ruling **bank_import:R-GW** the deposit card off
                :attr:`recordable_inflows`.  What every caller does share is
                that the line was in THIS pass, which is what makes
                :attr:`declined_lines` answerable for it.

        Returns:
            One sentence naming the gap, for the receipt that has to say what
            it withheld and for the screen that has to say why a line is still
            there; ``None`` when this pass searched exhaustively for a
            counterpart to *line* and found none.
        """
        declined = self.declined_lines.get(line.line_id)
        if declined is not None:
            return declined
        crowded = [
            day for day in self.bounds.crowded_days
            if abs((day - line.posted_on).days) <= DAY_WINDOW
        ]
        if crowded:
            return (
                f"{crowded[0]} held too many rows for the app to search them "
                f"for a group that adds up to this line"
            )
        if self.bounds.unpriceable_count:
            return (
                f"{self.bounds.unpriceable_count} row(s) on this account "
                f"could not be priced, so the app could not compare them "
                f"against this line"
            )
        return None


def _covered_span(account_id: int) -> "tuple[date, date] | None":
    """Return the first and last day this account has a recorded line for.

    Every RECORDED line, matched or not: the span a statement covers is a fact
    about what the bank sent, and it must not move as the owner works through
    the matches.

    Args:
        account_id: The account.

    Returns:
        ``(first, last)``, or ``None`` when nothing is recorded.
    """
    bounds = db.session.query(
        db.func.min(BankStatementLine.posted_on),
        db.func.max(BankStatementLine.posted_on),
    ).filter(BankStatementLine.account_id == account_id).one()
    return None if bounds[0] is None else (bounds[0], bounds[1])


def _could_have_been_shown(
    row: CandidateRow, covered: "tuple[date, date] | None",
) -> bool:
    """Return whether the statement could have shown *row*'s movement.

    **It asks the row's own WINDOW** -- the days the app believes that money
    moved between (:attr:`~._offers.CandidateRow.expected_window`) -- and the
    two overlap or they do not.  It used to test one day, ``settled_on or
    expected_on``, which is that accessor's own rule written a second time and
    one end short: a bill budgeted across the statement's opening day was
    dropped from the list because its period STARTS earlier, while every rule
    beside it had learned that a bill occupies a fortnight.  Found by
    adversarial design review 2026-08-19.

    Args:
        row: The candidate.
        covered: The recorded span, or ``None`` when nothing is recorded.

    Returns:
        Whether the two spans overlap.  A row the app can date no way at all is
        IN: there is no basis for excluding it, and saying so is better than
        dropping it silently -- the opposite of the proposer's answer for the
        same row, and deliberately, because this list is a REPORT and that one
        is a money door.
    """
    if covered is None:
        return False
    window = row.expected_window
    if window is None:
        return True
    return window[0] <= covered[1] and window[1] >= covered[0]


def _spoken_for(account_id: int):
    """Return the query naming *account_id*'s lines a match already claims.

    One definition of "spoken for", because two readers ask it: the review
    screen's own list (:func:`_unmatched_lines`) and the cheap count the grid
    renders (:func:`awaiting_review_count`).  A count that answered a
    different question from the list it links to would be a figure
    disagreeing with its caption.

    Args:
        account_id: The account.

    Returns:
        The query of claimed ``bank_statement_line_id`` values.
    """
    return (
        db.session.query(StatementMatchMember.bank_statement_line_id)
        .filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
            # ...and the act still names an app row, or the membership is not a
            # claim about anything (:func:`~._candidates.act_still_names_a_row`,
            # which carries the argument and the measurement).  The SAME clause
            # ``matched_subjects`` applies, so the screen's list, the grid's
            # count and the offer set cannot disagree about what "explained"
            # means.
            act_still_names_a_row(),
        )
    )


def awaiting_review_count(account_id: int, opens: "date | None") -> int:
    """Return how many recorded lines the review has NOT disposed of.

    The grid's bank statement control renders this figure, so it is a COUNT
    in the database rather than ``len`` over hydrated rows: the screen it
    links to is the expensive one (:meth:`~._scope.ReviewScope.build`), and a
    control on the app's hottest render path may not pay for it.

    **It applies exactly the two predicates
    :func:`review_set` splits on, and no others**, which is what lets the
    number and the screen agree:

    * no accepted match names the line (:func:`_spoken_for`), and
    * the line is not BEFORE the owner's first payday
      (:func:`_split_at_calendar_open`), because a line the pay calendar
      never covers can never be matched -- 130 of 361 on the developer's own
      export -- and counting those would leave the figure permanently
      non-zero and therefore meaningless.

    It deliberately does NOT run the proposer.  A line a proposal explains is
    still work until the owner ACCEPTS it, so the count is the whole of the
    screen's ``proposals`` plus its ``unmatched`` -- every line the review is
    still asking about.

    Args:
        account_id: The account.
        opens: The first day the owner's pay calendar covers
            (:meth:`~app.services.pay_calendar.PayCalendar.opening_bound`),
            or ``None`` for an owner with no periods at all -- in which case
            nothing is before the calendar, matching
            :func:`_split_at_calendar_open`.  **Taken as a parameter rather
            than derived here**: the grid route already holds the pass's
            memoized calendar, and a producer below the route reads the
            pass's derivation instead of building a second one.

            *The second half of that reason has been retired.*  It read "a
            second one that can disagree with it under READ COMMITTED", and
            this function's only caller is ``grid/page._bank_control`` on the
            ``GET /grid`` render, where since plan step balance:X-i3 a second
            derivation here could not disagree with the route's.

            **And ``/grid`` is the ONE route where "the whole request is one
            snapshot" would be false**, which is why that is not the sentence:
            it opens a :func:`~app.db_transaction.write_transaction` block for
            the rolling top-up, so it runs read-only, then writable, then
            read-only again over a NEW snapshot.  What holds is narrower and
            positional -- the block is the first statement of ``index()``, and
            both the route's memoized calendar and this count are read long
            after it, so both fall inside the same later snapshot.  A
            ``write_transaction`` moved between those two reads would make the
            disagreement expressible again.

            What survives unconditionally is the reason that never depended on
            the isolation level: two reads of one fact in one request is this
            project's DRY violation, and the read this replaced was a second
            ``budget.pay_periods`` scan on the app's hottest render path.

    Returns:
        The count, ``0`` when the account has nothing recorded.
    """
    query = db.session.query(db.func.count(BankStatementLine.id)).filter(
        BankStatementLine.account_id == account_id,
        BankStatementLine.id.notin_(_spoken_for(account_id)),
    )
    if opens is not None:
        query = query.filter(BankStatementLine.posted_on >= opens)
    return query.scalar() or 0


def _unmatched_lines(account_id: int) -> "list[BankStatementLine]":
    """Return the account's recorded lines that no match explains.

    Args:
        account_id: The account.

    Returns:
        The lines, ascending by posted day then id.
    """
    spoken_for = _spoken_for(account_id)
    return (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.notin_(spoken_for),
        )
        .order_by(BankStatementLine.posted_on, BankStatementLine.id)
        .all()
    )


def _as_bank_line(row: BankStatementLine) -> BankLine:
    """Return *row* as the value the proposer and the screen share.

    Args:
        row: A recorded line.

    Returns:
        Its :class:`~._offers.BankLine`.
    """
    return BankLine(
        line_id=row.id,
        posted_on=row.posted_on,
        amount=Decimal(str(row.amount)),
        description=row.description,
        transaction_on=row.transaction_on,
        merchant_id=row.merchant_id,
        merchant=row.merchant_name,
    )


def _split_at_calendar_open(
    recorded: "list[BankStatementLine]", opens: "date | None",
) -> "tuple[list[BankStatementLine], list[BankStatementLine]]":
    """Split the account's unmatched lines at the owner's first payday.

    Args:
        recorded: Every recorded line no match explains.
        opens: The first day the pay calendar covers, or ``None`` for an owner
            with no periods at all -- in which case nothing is BEFORE, because
            "before the calendar" is not a fact about a calendar that does not
            exist, and the lines are reported as unexplained rather than as
            out of reach.

    Returns:
        ``(before, inside)``.  A line before the first payday can never be
        matched -- there are no rows before that day for it to match -- so it
        is COUNTED by :class:`ReviewBounds` rather than listed as work.
    """
    before = [
        line for line in recorded
        if opens is not None and line.posted_on < opens
    ]
    before_ids = {line.id for line in before}
    return before, [line for line in recorded if line.id not in before_ids]


def _rows_the_bank_never_showed(
    offerable: "list[CandidateRow]",
    proposals: "tuple[MatchProposal, ...]",
    account_id: int,
) -> "tuple[CandidateRow, ...]":
    """Return the app's own rows the statement could have shown and did not.

    Ruling **R-FP**'s other side, and the more valuable half for a budget: a row
    the bank never showed, and would have shown as a line of its own, is a
    payment the records claim happened and the bank did not make.

    **This answers "did any line explain it", never "should the bank have shown
    it separately"**, and conflating the two is what plan step
    ``bank_import:X-gc`` corrected on the screen rather than here.  A CC
    payback's money leaves inside one payment to the card and an envelope's
    inside its own purchases, so neither is ever a line of its own -- and both
    stay in this list, because it is the hand-build form's row-picker and those
    paybacks are what a parked Capital One line is grouped against.  The screen
    withdraws the inference per row through
    :attr:`~._offers.CandidateRow.not_shown_alone`; withdrawing MEMBERSHIP
    would have closed ruling **R-GJ**'s only remaining arm.

    **The span is every RECORDED line's, not the unmatched ones'.**  Taking it
    from the leftovers made the window SHRINK as matches were accepted --
    matching the earliest or latest line silently dropped app rows from the
    list, and matching every line left no span at all -- while the card went on
    claiming these "fall inside the span your statement covers".

    **A row is measured on the WINDOW the app expects it in**, which is its
    settle day where it has one and its projection's span where it does not.
    Using "undated is always in" put every forward projection on the account
    into the list: 712 rows on the developer's own, most of them dated months
    ahead.  A projection the bank could not yet have shown is not a payment the
    bank failed to make.  Both found by adversarial review 2026-08-17.

    Args:
        offerable: The candidate rows no accepted match has claimed.
        proposals: What this pass proposes, whose rows are already explained.
        account_id: The account, for the recorded span.

    Returns:
        The rows, in the candidate order.
    """
    spoken_for = {
        (row.kind, row.row_id)
        for proposal in proposals for row in proposal.rows
    }
    covered = _covered_span(account_id)
    return tuple(
        row for row in offerable
        if (row.kind, row.row_id) not in spoken_for
        and _could_have_been_shown(row, covered)
    )


def _unexplained(
    bank_lines: "list[BankLine]", proposals: "tuple[MatchProposal, ...]",
) -> "list[BankLine]":
    """Return the lines no proposal in this pass accounts for.

    Args:
        bank_lines: Every line inside the calendar that no accepted match
            already explains.
        proposals: What this pass proposes.

    Returns:
        The leftovers, in the order given.
    """
    explained = {
        line.line_id for proposal in proposals for line in proposal.lines
    }
    return [line for line in bank_lines if line.line_id not in explained]


def review_set(scope: ReviewScope) -> ReviewSet:
    """Return everything the review screen shows for one account.

    ONE assembly, so the proposals, the leftovers and the bounds are all
    derived from the same read of the same account inside one request -- a
    screen whose "unmatched" list came from a second pass could disagree with
    its own proposals.

    **The SCOPE is a parameter since plan step ``bank_import:X-f6a-3c-2``**, so
    the screen and the doors it posts to derive an account once between them
    rather than once each.  This reader built its own until then, and the batch
    door's response re-renders this same set: two derivations of 827 priced rows
    in one request, at 3.593 s apiece.

    Args:
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).

    Returns:
        Its :class:`ReviewSet`.
    """
    calendar = scope.calendar
    account_id = scope.account_id
    opens = calendar.opening_bound()
    before, inside = _split_at_calendar_open(
        _unmatched_lines(account_id), opens,
    )

    # **What is already CLAIMED is read HERE, not carried in the scope.**  The
    # doors re-read it per act, and this screen is rendered after them inside
    # the same request, so a reader taking it off the scope would list rows the
    # batch had just matched.
    matched = matched_subjects(account_id)
    candidates = scope.candidates
    offerable = unmatched_rows(candidates, matched)
    bank_lines = [_as_bank_line(line) for line in inside]
    proposed = propose(bank_lines, offerable)
    proposals = proposed.proposals
    unmatched = _unexplained(bank_lines, proposals)
    parts = leftovers(
        scope, unmatched,
        unmatched_destinations(scope.destinations, matched),
    )
    return ReviewSet(
        proposals=proposals,
        unmatched=tuple(unmatched),
        unmatched_rows=_rows_the_bank_never_showed(
            offerable, proposals, account_id,
        ),
        accepted=tuple(accepted_groups(scope.owner_id, account_id)),
        creatable=parts.creatable,
        parked=parts.parked,
        recordable_inflows=parts.recordable_inflows,
        merchants=parts.merchants,
        bounds=ReviewBounds(
            calendar_opens=opens,
            before_calendar_count=len(before),
            before_calendar_last_day=(
                max(line.posted_on for line in before) if before else None
            ),
            # **The days the SEARCH skipped, published by the search** (finding
            # **N-322**).  This reader re-derived them over every candidate
            # until plan step X-f6a-3c-2, while ``propose`` searches only the
            # rows no one-to-one proposal claimed -- a superset, so the screen
            # could name a day too crowded to search that had been searched.
            crowded_days=proposed.crowded_days,
            unpriceable_count=len(candidates.unpriceable_ids),
            impossible_day_count=parts.impossible_day_count,
        ),
        # **The near tier's own bound, published by the pass that applied it**,
        # for the reason the crowded days beside it are: a reader re-deriving
        # it would be scoring a different population.  It sits on the SET
        # rather than in ``bounds`` because the screen renders it against the
        # LINE it concerns rather than in the panel of things this page did not
        # look at (plan step ``bank_import:X-f6d-3``).
        declined_lines=proposed.declined_lines,
    )
