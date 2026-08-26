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

from dataclasses import dataclass, replace
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
from ._creations import PurchaseDestination, envelope_answer_key
from ._offers import (
    BankLine,
    CandidateRow,
    MatchProposal,
)
from ._bars import CreationBars, ParkedLine
from ._placement import Placement, placements_for
from ._rules import RuleView
from ._propose import propose
from ._scope import ReviewScope
from ._section import MerchantSection, merchant_section


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
    (:attr:`ReviewSet.undecided_near_lines`), where the act it should prompt is
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
class CreatableLine:
    """One bank OUTFLOW the app has no row for, and where it could go.

    Plan step ``bank_import:X-f6a-3b``, ruling **R-FS**'s third shape.  These
    are the lines the matcher can never explain, because the app records a
    period's groceries as one envelope and the bank records every swipe:
    measured on the developer's own statement **91** unmatched outflows survive
    every proposal, of which 74 are card swipes worth `$3,383.49` -- the case
    R-FS names -- and 17 are ACH debits the app may already hold in another
    shape, which the screen SAYS rather than filtering on the bank's prose.

    Attributes:
        line: The bank's own record of the movement.
        pay_period_id: The period covering the day the bank says it was MADE,
            or ``None`` when no saved period does -- which is what a line
            older than the owner's first payday looks like.  The MADE day and
            not the posting day, because a purchase's budget clock is
            ``purchased_on`` and a swipe made on a period's last day and posted
            on the next period's first belongs to the budget it was made under.
        destinations: The budget lines it could become a purchase against, in
            that period.  EMPTY is a real answer and the screen must say so
            rather than rendering a chooser with nothing in it: on the
            developer's own data the 2026-03-26 period holds three envelopes and
            all three closed at a fixed figure, so 8 lines worth `$662.13` have
            no existing destination and a NEW envelope is the only arm open to
            them.
        placement: What the owner's stated MERCHANT RULE comes to for this
            line (:class:`~._rules.Placement`), or ``None`` when they have not
            said where this merchant goes -- which is a different answer from
            "they said never" and the screen says it differently.  Plan step
            ``bank_import:X-f6a-3d``.
            **It is a SUGGESTION and never a tick**: the destination select
            still opens on *leave this line alone*, and what turns a placement
            into an act is the owner pressing the sweep.  A remembered
            destination that arrived already selected would be a default
            pointing at money, which is what ruling **R-FZ** removed.
    """

    line: BankLine
    pay_period_id: "int | None"
    destinations: "tuple[PurchaseDestination, ...]"
    placement: Placement | None = None


@dataclass(frozen=True)
class ReviewSet:  # pylint: disable=too-many-instance-attributes
    """Everything the review screen needs, in one value.

    Pylint: too-many-instance-attributes (9/7) -- **nine because the screen
    renders nine distinct things**, not because the value wants splitting.
    Eight are cards the owner reads and acts in; the ninth is
    :attr:`undecided_near_lines`, which annotates two of them.

    The obvious way to satisfy the limit is to fold ``undecided_near_lines``
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
            card refund can only ever be matched to a row.  **A line that
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
        merchants: The rule control (:class:`~._section.MerchantSection`) --
            where this account's merchants go, and what the owner has already
            said.  **It counts** ``parked`` **beside** ``creatable``, because
            the parked half is parked for want of an answer and this is the
            control that gives one.
        bounds: What this pass did NOT look at (:class:`ReviewBounds`).
        undecided_near_lines: The ids of the lines the NEAR tier admitted a
            candidate for and then declined to choose between
            (:attr:`~._propose.ProposedMatches.undecided_near_lines`).

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
    merchants: MerchantSection
    bounds: ReviewBounds
    undecided_near_lines: "frozenset[int]" = frozenset()

    @property
    def placed_by_class(self) -> "dict[str, int]":
        """Return how many creatable lines each sweep CLASS would tick.

        **Counted where the sweep's own rule is**
        (:attr:`~._rules.Placement.sweep_class`) rather than as a Jinja
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
            pass's derivation instead of building a second one that can
            disagree with it under READ COMMITTED.

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


def _creatable_lines(
    calendar, unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
    view: RuleView,
    bars: CreationBars,
) -> "tuple[tuple[CreatableLine, ...], tuple[ParkedLine, ...], int]":
    """Split the unmatched OUTFLOWS into what may be recorded and what may not.

    **A line the bank dates MADE after it POSTED is not one of them** (finding
    **N-325**, developer ruling 2026-08-19).  ``entry_service.create_entry``
    refuses a purchase whose money left before it was spent, so such a line's
    destination chooser is a control whose submission can never succeed; it is
    counted on :class:`ReviewBounds` instead, beside every other thing this
    pass did not look at.  The rejected remedy was clamping the purchase day to
    the earlier of the two, which decides which day the app believes when the
    bank contradicts itself -- the substitution ruling **R-FW** refused one
    clock over.  The predicate is
    :attr:`~._offers.BankLine.states_impossible_days`, stated once because
    :func:`~._pairing.within_window` asks it too.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places each
            line.  Taken rather than loaded, so one request holds ONE calendar
            (:func:`review_set`).
        unmatched: The bank lines inside the calendar no proposal explains.
        destinations: Every offerable budget line
            (:func:`~._candidates.destinations_for`, narrowed by
            :func:`~._candidates.unmatched_destinations`), read ONCE for the
            whole pass and grouped here rather than
            re-queried per line -- a redundant producer call inside one request
            is this project's DRY violation rather than a cost.
        view: What the owner has said and what it can resolve against
            (:class:`~._rules.RuleView`).
        bars: Which of this account's merchants may not become purchases, and
            why (:class:`~._bars.CreationBars`, ruling **R-GJ**).  **Asked
            BEFORE a destination is resolved**, because a barred line has no
            destination to resolve one against: the placement machinery answers
            *which budget line would this go in*, and for these the answer is
            that none may, which is a refusal rather than a suggestion.

    Returns:
        ``(creatable, parked, impossible_day_count)`` -- one
        :class:`CreatableLine` per offerable outflow a create control may be
        rendered for, one :class:`~._bars.ParkedLine` per outflow ruling
        **R-GJ** bars, both in the order the lines were given, and how many
        were declined for dating their own purchase after their own posting.
        The per-period destination tuple is SHARED by every line in that
        period, so a statement with 91 outflows over 11 periods builds 11
        tuples rather than 91.
    """
    outflows = [line for line in unmatched if line.amount < 0]
    impossible = [line for line in outflows if line.states_impossible_days]
    offerable = [line for line in outflows if not line.states_impossible_days]
    if not offerable:
        return (), (), len(impossible)
    # ONE pass over the destinations, and ONE placement per line.  Both were
    # asked twice: the grouping rescanned every destination once per period,
    # and each line placed itself once for its id and again for its lookup --
    # 182 calls for 91 outflows.  A redundant producer call inside one request
    # is this project's DRY violation rather than a cost.
    by_period: "dict[int, list[PurchaseDestination]]" = {}
    for destination in destinations:
        by_period.setdefault(destination.pay_period_id, []).append(destination)
    creatable: "list[CreatableLine]" = []
    parked: "list[ParkedLine]" = []
    for line in offerable:
        # ONE ask of the bar per line, and its answer is what routes the line:
        # a second ask -- once to partition and once to word the sentence -- is
        # the redundant producer call this module already refuses above.
        barred_by = bars.bar_for(line.merchant_id)
        if barred_by is not None:
            parked.append(ParkedLine(line=line, barred_by=barred_by))
            continue
        creatable.append(_one_creatable(
            line, _period_id_for(calendar, line.happened_on), by_period, view,
        ))
    return _marked_joining(creatable), tuple(parked), len(impossible)


def _marked_joining(
    creatable: "list[CreatableLine]",
) -> "tuple[CreatableLine, ...]":
    """Flag each line that would JOIN an envelope an earlier line creates.

    **A press mints ONE envelope per answer per pay period**
    (:class:`~._create.MintedEnvelopes`, finding **N-327**), so the second and
    later lines of one new-envelope answer file into the first one's envelope
    rather than making more beside it.  The screen has to say that BEFORE the
    press, and this is the only reader that sees more than one line at a time
    -- ``placements_for`` resolves one line against its own period and cannot
    know what another line in the same pass will do.

    Walked in the order the lines are given, which is the order the sweep
    applies them, so under the SWEEP -- which ticks a whole class -- the line
    left unflagged is the one that creates.

    **It reads what is OFFERED, not what the owner will tick**, and the
    distinction is real: every select opens on *leave this line alone*, so if
    they hand-pick a flagged line and leave the unflagged one alone, the line
    told "an earlier line here already creates it" is the line that creates.
    The outcome is still one envelope per answer per period, so no money turns
    on it; what the sentence can be wrong about is WHICH line does the
    creating.  Named by two adversarial reviews 2026-08-20.

    Args:
        creatable: The pass's offerable outflows, in order.

    Returns:
        The same lines, with :attr:`~._placement.Placement.joins_new` set on
        every one after the first for its answer and period.
    """
    creating: "set[tuple[str, int, int]]" = set()
    marked = []
    for line in creatable:
        placement = line.placement
        if placement is not None and placement.creates:
            key = envelope_answer_key(
                placement.new_envelope, line.pay_period_id,
            )
            if key in creating:
                line = replace(
                    line, placement=replace(placement, joins_new=True),
                )
            creating.add(key)
        marked.append(line)
    return tuple(marked)


def _one_creatable(
    line: BankLine,
    period_id: "int | None",
    by_period: "dict[int, list[PurchaseDestination]]",
    view: RuleView,
) -> CreatableLine:
    """Return one offerable outflow with its destinations and its placement.

    Args:
        line: The bank line.
        period_id: The pay period covering the day it was MADE, or ``None``.
        by_period: The offerable destinations, grouped by period.
        view: What the owner has said and what it can resolve against.

    Returns:
        Its :class:`CreatableLine`.  A line no saved period covers gets NO
        placement, because a rule resolves into a destination and there is no
        period here for one to be in -- the create door refuses such a line by
        name (``_create._period_holding``), so suggesting anything for it would
        be the chooser-that-cannot-succeed shape again.
    """
    offered = by_period.get(period_id, [])
    return CreatableLine(
        line=line,
        pay_period_id=period_id,
        destinations=tuple(offered),
        placement=(
            None if period_id is None
            else placements_for(line.merchant_id, view, offered)
        ),
    )


def _period_id_for(calendar, day: date) -> "int | None":
    """Return the SAVED pay period covering *day*, or ``None``.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
        day: The day the bank says the purchase was made.

    Returns:
        Its period id, or ``None`` when no saved period covers it -- a line
        older than the owner's first payday, or past the generated horizon.
    """
    period = calendar.period_containing(day)
    return None if period is None else period.period_id


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


@dataclass(frozen=True)
class _Leftovers:
    """What this pass could not explain, placed against the owner's rule.

    Four facts one derivation produces that travel together, which is the
    argument :class:`~._offers.Candidates` and
    :class:`~._propose.ProposedMatches` already make in this package: a caller
    holding the offerable lines without the count of the ones declined would
    render a list that reads as complete.

    Private, because what leaves this module is :class:`ReviewSet`.

    Attributes:
        creatable: The offerable unexplained outflows a create control may be
            rendered for, each with its placement.
        parked: The offerable unexplained outflows ruling **R-GJ** bars, each
            with the reason (:class:`~._bars.ParkedLine`).
        merchants: The rule control's rows and option list.
        impossible_day_count: How many outflows were declined for being dated
            MADE after they POSTED (finding **N-325**).
    """

    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[ParkedLine, ...]"
    merchants: MerchantSection
    impossible_day_count: int


def _leftovers(
    scope: ReviewScope,
    unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
) -> _Leftovers:
    """Return the unexplained outflows placed against the owner's rule.

    **What the owner has SAID is read HERE, not carried on the scope**, for the
    same reason the claims are (plan step ``bank_import:X-f6a-3d``): a pass can
    restate a rule, and this screen is re-rendered after the door that does,
    so a reader taking it off the scope would show the answers the pass had
    just replaced.  Ruling **R-GJ**'s bars are read at the same instant and for
    the same reason -- one of them IS an answer, and the other is the absence
    of one -- and from the answers this view has already read, so one request
    asks ``merchant_rules`` once.

    Args:
        scope: The pass's derived offer set.
        unmatched: The bank lines inside the calendar no proposal explains.
        destinations: The budget lines still open to a new purchase.

    Returns:
        The :class:`_Leftovers`.
    """
    view = RuleView.build(scope.owner_id, scope.account_id)
    bars = CreationBars.build(
        scope.owner_id, scope.account_id, rules=view.rules,
    )
    creatable, parked, impossible_days = _creatable_lines(
        scope.calendar, unmatched, destinations, view, bars,
    )
    return _Leftovers(
        creatable=creatable,
        parked=parked,
        # **Both halves**, because a merchant is parked for want of an answer
        # and this control is the only place one is given: counting only the
        # creatable half would refuse the act and hide the door that permits
        # it.
        merchants=merchant_section(
            [item.line for item in creatable] + [item.line for item in parked],
            view, bars,
        ),
        impossible_day_count=impossible_days,
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
    leftovers = _leftovers(
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
        creatable=leftovers.creatable,
        parked=leftovers.parked,
        merchants=leftovers.merchants,
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
            impossible_day_count=leftovers.impossible_day_count,
        ),
        # **The near tier's own bound, published by the pass that applied it**,
        # for the reason the crowded days beside it are: a reader re-deriving
        # it would be scoring a different population.  It sits on the SET
        # rather than in ``bounds`` because the screen renders it against the
        # LINE it concerns rather than in the panel of things this page did not
        # look at (plan step ``bank_import:X-f6d-3``).
        undecided_near_lines=proposed.undecided_near_lines,
    )
