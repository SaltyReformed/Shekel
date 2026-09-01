"""What the review queue SHOWS, and which decision each line poses.

Ruling **bank_import:R-HB**, plan step ``bank_import:X-gf-3b-2``.  The review
screen used to partition its unexplained lines by MECHANISM across three cards
-- an outflow that may be recorded, an outflow ruling **R-GJ** bars, an inflow
that may be recorded.  That partition is the SERVICE's and it is the right one,
because the three reach different doors; it is not the READER's, because all
three pose one question: *is this money my books already hold, or is it new?*

**So the mechanism stays load-bearing underneath and stops being visible on
top.**  This module groups the same three lists by what the EVIDENCE says, and
each row still carries whatever act its own mechanism opens -- a destination
select, a tick, or no control at all.

**The grouping is on evidence the pass ALREADY derives, never on a new fact**
(the step's own constraint).  Two signals exist and both are read here rather
than re-derived:

* a POSITIVE counterpart signal -- ruling **R-GJ**'s bar on an outflow, or
  income the books already record for an inflow's own pay period
  (:meth:`~._reads.ReviewSet.income_already_recorded_in`);
* an UNFINISHED search -- :func:`~._gaps.search_gap`, which says why this pass
  cannot conclude the line has no counterpart.

**Measured on the developer's own data 2026-08-28**, through the real producer
on a clone at migration head: **17** lines carry a positive signal (9 Capital
One payments at `-$7,412.94`, 8 deposits whose period already holds unexplained
income), **10** carry none, and **0** carry an unfinished search WITHOUT a
positive signal -- which is why :attr:`Evidence.UNFINISHED` renders empty
today.  It is built anyway because the predicate is real and the data is not:
all five gap-carrying lines happen to also carry a positive signal, and that is
a fact about one statement rather than about the shape.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- every fact
it holds arrives from the pass that measured it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rules import is_inflow
from ._verdict import look_first

if TYPE_CHECKING:  # pragma: no cover -- the edge back would be a cycle
    # The three mechanism values appear only in annotations, so they are
    # imported for the type checker rather than at runtime.
    from ._bars import ParkedLine
    from ._offers import BankLine
    from ._leftovers import CreatableLine, RecordableInflow
    from ._reads import IncomeAlreadyRecorded, ReviewSet
    # :mod:`._reads` imports THIS module for :attr:`~._reads.ReviewSet.queue`,
    # so the annotation is a forward reference and the import is type-checking
    # only.  The direction is right: the queue is assembled from a pass that
    # already exists.


class Evidence(enum.Enum):
    """What this pass's own evidence says about one unexplained line.

    Ruling **bank_import:R-HB**'s ordering axis: the reader answers *is this
    money my books already hold, or is it new?*, and these are the three
    answers the evidence can point to.  **It is the EVIDENCE and never the
    ACT** -- what a line may become is its mechanism's business and is carried
    on the row beside this.

    * ``ALREADY_HELD`` -- something of the owner's points at this line.  For an
      outflow that is ruling **R-GJ**'s bar, which says this merchant's money
      is a payment to an account they hold or is spending they have said is
      never a purchase; for an inflow it is unexplained income the books
      already record for the line's own pay period.  The indicated act is to
      MATCH, and recording is the duplicate this arc measures.
    * ``UNFINISHED`` -- no positive signal, but the pass cannot say there is no
      counterpart either: some tier admitted a candidate and would not choose
      it, or a day was too crowded to search (:func:`~._gaps.search_gap`).
    * ``NOTHING_FOUND`` -- the pass searched exhaustively and found nothing, so
      recording adds what the books genuinely did not have.

    **The order of the members is the order the queue renders them**, riskiest
    first: the group where the wrong act costs money comes before the group
    where a one-click sweep is offered, so the owner disposes of the duplicates
    before reaching the bulk control.
    """

    ALREADY_HELD = "already_held"
    UNFINISHED = "unfinished"
    NOTHING_FOUND = "nothing_found"


#: The heading and the one sentence under it, per group.  **Server-derived and
#: printed unbranched**, which is the rule :attr:`~._bars.ParkedLine.reason`
#: and :attr:`~._leftovers.RecordableInflow.withheld` both exist to state: the
#: partition is this module's, and a template picking the wording with
#: ``{% if %}``/``{% elif %}`` would be a second place for it to be wrong.
_SAID: "dict[Evidence, tuple[str, str]]" = {
    Evidence.ALREADY_HELD: (
        "Your records may already hold these",
        "Something of yours already points at each of these, and the reason "
        "is beside the line. Where it is the same money, match it against the "
        "rows you hold rather than recording it again.",
    ),
    Evidence.UNFINISHED: (
        "The app could not finish checking these",
        "For each of these the app either found something it could not rule "
        "out, or could not finish looking. Either way it cannot say nothing "
        "of yours explains them. The reason is beside the line.",
    ),
    Evidence.NOTHING_FOUND: (
        "Nothing of yours accounts for these",
        "The app compared each of these against the rows your records hold "
        "and found none that matches. It cannot see money your budget holds "
        "in another shape -- a premium you record as a bill, a subscription "
        "inside a bigger envelope -- so check that before recording one.",
    ),
}


#: The sweep classes, in the order the queue offers them and with the sentence
#: each is captioned by.  **The order is by RISK**, which is ruling
#: **R-FZ(c)**'s own: filing into a budget line that is still open is absorbed
#: by what it reserved, raising what a CLOSED one recorded changes a figure the
#: owner had finished with, and minting an envelope the account did not have is
#: the one act releasing the match cannot undo.
_SWEEP_LABELS: "tuple[tuple[str, str], ...]" = (
    ("into_open", "into a budget line that is still open"),
    ("into_closed", "into one that has already closed, raising what it recorded"),
    ("creates", "into a NEW envelope this would create"),
)


class QueueAct(enum.Enum):
    """Which control one queue row renders.

    The MECHANISM partition, which ruling **bank_import:R-HB** keeps
    load-bearing while taking it off the screen as a grouping.  Exposed to the
    template as the three booleans on :class:`QueueRow` rather than compared
    against there, which is the idiom :class:`~._placement.Placement` already
    sets with :attr:`~._placement.Placement.records_in`.
    """

    RECORD_PURCHASE = "record_purchase"
    RECORD_INCOME = "record_income"
    NONE_OPEN = "none_open"


@dataclass(frozen=True)
class QueueRow:
    """One unexplained bank line, in its evidence group, with its own act.

    **The act is STATED and never inferred from an absence.**  The builder
    knows which list it drew a line from, so it says so; a reader testing
    ``if item.destinations`` would be reading a control out of an empty
    collection, which is the defect that made the existing-envelope arm
    unreachable from a browser at plan step ``X-f6a-3b`` and the shape
    :class:`~._bars.ParkedLine` refuses in as many words.

    Attributes:
        evidence: Which group this row sits in (:class:`Evidence`).
        item: The mechanism's own value -- a
            :class:`~._leftovers.CreatableLine`, a
            :class:`~._bars.ParkedLine` or a
            :class:`~._leftovers.RecordableInflow`.  It is NOT flattened into
            one record carrying every field: a value holding empty destinations
            beside an empty withheld sentence would be a control one Jinja
            condition away from rendering, which is the argument
            :class:`~._bars.ParkedLine` already makes for existing separately.
        act: Which control this row renders (:class:`QueueAct`).
        notes: Every plain sentence this row owes the reader, in reading
            order, composed by :func:`_notes_for`.  **Printed unbranched**,
            which is the whole reason it exists: the three cards this replaced
            each composed their own evidence sentences in Jinja, so the
            template held three different answers to *why is this line still
            here* and a line could only ever get the one its card knew about.
            That is how a PARKED line came to be the one kind that never
            printed its search gap -- the asymmetry ruling **bank_import:R-HB**
            names, measured at 1 of the developer's 9 parked lines.
        income_already_held: What the books already record as unexplained
            income for this line's own pay period
            (:class:`~._reads.IncomeAlreadyRecorded`), or ``None``.  **Carried
            rather than re-asked**: the builder reads it to decide this row's
            group, and a template asking again would be the redundant producer
            call inside one request that this package treats as a DRY
            violation rather than a cost.  It is not a sentence because it
            NAMES ROWS and states a figure, and money is formatted by the
            ``money`` macro rather than by a service.
    """

    evidence: Evidence
    item: "CreatableLine | ParkedLine | RecordableInflow"
    act: QueueAct
    notes: "tuple[str, ...]"
    income_already_held: "IncomeAlreadyRecorded | None"

    @property
    def line(self) -> "BankLine":
        """Return the bank's own record of the movement.

        Returns:
            The :class:`~._offers.BankLine`, which all three mechanisms carry
            under the same name -- so the half of the row that states what the
            bank said is rendered once rather than three times.
        """
        return self.item.line

    @property
    def records_a_purchase(self) -> bool:
        """Return whether this row renders the destination chooser.

        Returns:
            ``True`` for an offerable outflow, which is the only act that files
            money into a container the owner picks between.
        """
        return self.act is QueueAct.RECORD_PURCHASE

    @property
    def records_a_refund(self) -> bool:
        """Return whether the purchase this row would file is a REFUND.

        Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b-3``.
        Money ARRIVING that a rule files into a container is a refund, and the
        screen owes it a different sentence: the press-level paragraph said
        every line it records becomes *a purchase your records did not have,
        dated the day your bank took it*, which is false of money the bank gave
        back.

        **A PROPERTY here where :attr:`~._panel.AddTab.records_a_refund` is a
        FIELD**, and the asymmetry is which value holds the line.  That one
        does not, so its builder must state the answer; this one carries the
        line under :attr:`line`, so deriving it is what makes the two unable to
        disagree.  Both ask :func:`~._rules.is_inflow`, which is this package's
        ONE statement of the bank's sign convention -- the sign is not tested
        here, it is asked there.

        Returns:
            ``True`` for a row that files a purchase whose money arrives.
        """
        return self.records_a_purchase and is_inflow(self.line.amount)

    @property
    def records_a_charge(self) -> bool:
        """Return whether the purchase this row would file is a CHARGE.

        The complement of :attr:`records_a_refund` INSIDE the purchase arm, so
        the screen renders one sentence each with no ``else`` -- the same
        no-else partition :attr:`records_a_purchase` exists for, and the shape
        ``_statement_reconcile_macros.html`` already uses for the card.

        Returns:
            ``True`` for a row that files a purchase the bank took money for.
        """
        return self.records_a_purchase and not self.records_a_refund

    @property
    def records_income(self) -> bool:
        """Return whether this row renders the record-as-income tick.

        Returns:
            ``True`` for an inflow, which is filed against no container -- so
            the tick is the whole control and there is nothing to choose.
        """
        return self.act is QueueAct.RECORD_INCOME

    @property
    def offers_no_control(self) -> bool:
        """Return whether this row renders no write control at all.

        Returns:
            ``True`` for a line ruling **R-GJ** bars.  The closure is
            structural rather than advisory -- :func:`~._bars.reject_barred_line`
            refuses it at the door too -- and this is what keeps the row safe
            inside the same ``<form>`` as the controls above it.
        """
        return self.act is QueueAct.NONE_OPEN




@dataclass(frozen=True)
class QueueSweep:
    """One risk class of placed lines, and the one click that records them.

    Ruling **R-FZ(c)**, re-scoped at plan step ``bank_import:X-gf-3b-2`` on the
    developer's ruling of 2026-08-28.  **A sweep now belongs to the GROUP it is
    rendered in**, and only :attr:`Evidence.NOTHING_FOUND` has any.

    **What that closes:** the count was
    ``ReviewSet.placed_by_class``, over every creatable line with a placement
    and blind to whether that line also carried a warning that its money may
    already be recorded.  So the screen could print *the app found a row this
    might be* beside a line and still tick it under a one-click that ignored
    the sentence -- the *warning paragraph above a working control* shape
    ruling **R-GJ** cost `$7,412.94` to learn, at the grain of one line rather
    than one merchant.  Measured 2026-08-28 on the developer's own data: 0 of
    his 2 creatable lines carry both, so this was latent rather than live, and
    grouping the queue by evidence is what would have made it a contradiction
    on the page.

    The reach is now the DOM subtree the control lives in
    (``statement_review.js``), so a sweep cannot select a row outside its own
    group even if a later edit gave one a placement class.

    Attributes:
        css_class: The sweep class key, which is
            :attr:`~._placement.Placement.sweep_class`'s own spelling and the
            value the row carries in ``data-placement-class``.
        count: How many lines in THIS group ticking it would set.
        label: The sentence naming what the click does to them.
    """

    css_class: str
    count: int
    label: str


@dataclass(frozen=True)
class QueueGroup:
    """One evidence group: what it says, what is in it, and what it sweeps.

    Attributes:
        evidence: The group's own :class:`Evidence`.
        rows: Its lines, in the order the three source lists gave them --
            which is the order the bank recorded them, and the order a sweep
            applies (:func:`~._leftovers._marked_joining`).
        sweeps: The one-click controls this group offers
            (:class:`QueueSweep`), which is empty for every group but
            :attr:`Evidence.NOTHING_FOUND`.
    """

    evidence: Evidence
    rows: "tuple[QueueRow, ...]"
    sweeps: "tuple[QueueSweep, ...]"

    @property
    def heading(self) -> str:
        """Return the heading this group is printed under.

        Returns:
            The sentence, server-derived so the partition is stated once.
        """
        return _SAID[self.evidence][0]

    @property
    def explanation(self) -> str:
        """Return the one paragraph printed under the heading.

        Returns:
            The sentence, which carries what each of the three cards this
            replaced said about its own risk -- server-derived for the reason
            the heading is.
        """
        return _SAID[self.evidence][1]


@dataclass(frozen=True)
class StatementQueue:
    """The whole exception queue, as ONE list grouped by the decision.

    Ruling **bank_import:R-HB**.  It leaves this module for
    :attr:`~._reads.ReviewSet.queue`, which is what the screen renders.

    **It does NOT hold every line in** :attr:`~._reads.ReviewSet.unmatched`.
    An outflow the bank dates MADE after it POSTED reaches none of the three
    mechanisms -- :func:`~._leftovers._creatable_lines` drops it on finding
    **N-325**'s developer ruling -- so it stays in ``unmatched``, reaches no
    group, and is disclosed as :attr:`~._gaps.ReviewBounds.impossible_day_count`
    in the panel that states what this pass did not look at.  The conserved
    identity is therefore ``sum(len(group.rows)) + impossible_day_count ==
    len(unmatched)``, and a reader that assumed the simpler one would be
    wrong by exactly that class.  Measured 0 of the developer's 378 recorded
    lines; the OFX adapter's own measurement found 2 of 361, so a second
    source makes it live.

    Attributes:
        groups: The non-empty groups, riskiest first
            (:class:`Evidence`).  An empty group is ABSENT rather than
            rendered empty: a heading over no rows reads as work the owner has
            somewhere to do.
    """

    groups: "tuple[QueueGroup, ...]"

    @property
    def records_a_charge(self) -> bool:
        """Return whether any row here would file a purchase the bank TOOK.

        The press-level paragraph *what Apply will create* describes the acts
        this button performs, and it is rendered ONCE for the whole queue -- so
        it asks the QUEUE and not a row.  Stated here rather than composed in
        Jinja for the reason every other predicate on this page is: a template
        reducing over ``groups`` would be a second spelling of
        :attr:`QueueRow.records_a_charge`, and this file's own note on
        ``notes`` records what three spellings of one sentence cost.

        Returns:
            Whether the press would record at least one charge.
        """
        return any(
            row.records_a_charge
            for group in self.groups for row in group.rows
        )

    @property
    def records_a_refund(self) -> bool:
        """Return whether any row here would file a REFUND.

        :attr:`records_a_charge`'s twin, and the pair is what lets the
        press-level paragraph print one sentence per DIRECTION with no ``else``
        (plan step ``bank_import:X-gj-2b-3``).  A queue holding no creatable
        line at all answers ``False`` to both, which is right: nothing on that
        page is about to become a purchase in either direction.

        Returns:
            Whether the press would record at least one refund.
        """
        return any(
            row.records_a_refund
            for group in self.groups for row in group.rows
        )


def _evidence_for(gap: "str | None", positive: bool) -> Evidence:
    """Return which group one line belongs in.

    **The two signals are asked in RISK order and the first that holds wins**,
    because they are not exclusive: on the developer's own data all five
    gap-carrying lines also carry a positive signal, and grouping them by the
    weaker one would put a known duplicate under a heading that says the app
    merely did not finish looking.

    Args:
        gap: Why this pass could not conclude the line has no counterpart
            (:func:`~._gaps.search_gap`), or ``None``.  **Passed in rather than
            asked for here**, because the caller needs the same answer to
            compose the row's sentences and a second ask would be the redundant
            producer call this package refuses.
        positive: Whether a POSITIVE counterpart signal holds for this line --
            decided by the caller, because what counts as one differs by
            mechanism and each caller has the value that knows.

    Returns:
        Its :class:`Evidence`.
    """
    if positive:
        return Evidence.ALREADY_HELD
    if gap is not None:
        return Evidence.UNFINISHED
    return Evidence.NOTHING_FOUND


def _notes_for(
    item: "CreatableLine | ParkedLine | RecordableInflow",
    act: QueueAct, gap: "str | None",
) -> "tuple[str, ...]":
    """Return every plain sentence one row owes the reader, in reading order.

    **ONE composition for all three mechanisms**, which is what closes the
    asymmetry ruling **bank_import:R-HB** names.  Each of the three cards this
    replaced composed its own in Jinja, so *why is this line still here* had
    three answers on one page and a line got whichever its card knew about:
    the create card and the deposit card each printed
    :func:`~._gaps.search_gap` and the PARKED card did not, so the developer's
    `-$1,000.44` line of 2026-06-01 -- 1 of his 9 -- carried its bar reason on
    the queue and its gap only on the workbench.

    **The gap is not asked for a creatable line**, and that is not an omission:
    :func:`~._verdict.ruled` has already folded it into
    :attr:`~._leftovers.CreatableLine.warning`, which is the wider sentence --
    a rule this pass withheld, or a search it did not finish.  Asking again
    here would print the same words twice on the one mechanism whose value
    already carries them.

    **An inflow's gap carries the same framing verb an outflow's does**, and
    since plan step ``bank_import:X-gj-2b-3`` it carries the same SENTENCE:
    :func:`~._verdict.look_first`.  The deleted deposit card wrapped it in
    *before recording this as new income, match it against rows you already
    hold*, and printing the bare sentence would state a FACT where the outflow
    path states an ACT -- which is the per-mechanism asymmetry this step exists
    to end, reintroduced in the other direction.  This module then spelled the
    replacement out a SECOND time while its note claimed the two were one, and
    the two duly drifted: the outflow's said *as new spending* and this one did
    not, so ruling **bank_import:R-II** made one of them false about a refund
    and left the other correct.

    Args:
        item: The mechanism's value.
        act: Which control the row renders, which is what says how its
            sentences are composed -- stated by the caller rather than
            inferred from which fields the value happens to have.
        gap: This line's search gap, or ``None``.

    Returns:
        The sentences, in the order they are printed.  Empty when this pass has
        nothing to say about the line, which is the state that makes acting on
        it safe.
    """
    if act is QueueAct.RECORD_PURCHASE:
        return () if item.warning is None else (item.warning,)
    framed = None if gap is None else look_first(gap)
    first = item.reason if act is QueueAct.NONE_OPEN else item.withheld
    return tuple(
        sentence for sentence in (first, framed) if sentence is not None
    )


def _rows(review: "ReviewSet") -> "tuple[QueueRow, ...]":
    """Return every unexplained line as a queue row, evidence and act set.

    **Each of the three lists states its own act**, which is what keeps the
    mechanism partition load-bearing without it being inferred anywhere: the
    builder knows which list it is walking.

    **A parked line's positive signal is the BAR itself.**  Ruling **R-GJ**'s
    two arms are a merchant a source files as a payment to an account the owner
    holds, and one they have answered is never a purchase; both say this money
    is not new spending, and the remedy both leave is the group match.  So
    every parked line is :attr:`Evidence.ALREADY_HELD`, which is what the
    step's own measurement counted -- 9 of the developer's 9.

    **A creatable line's positive signal is a WITHHOLDING that is not a gap.**
    :func:`~._verdict.ruled` sets
    :attr:`~._leftovers.CreatableLine.warning` from THREE arms and only one of
    them is the search gap.  The second is
    :data:`~._verdict._ALREADY_EXPLAINED`, which fires when the rule's own
    destination is a row this statement explains AS A WHOLE; the third arrived
    with plan step ``bank_import:X-gj-2b`` and fires when a REFUND's pay period
    already holds money the records say arrived and no line explains.  **The
    test below reads the arms as a class rather than by name**, which is why a
    third one did not need this predicate changed -- but the sentence here said
    TWO, and a sentence that undercounts the thing a safety net protects is how
    the next arm gets added without one.  That is a
    counterpart the pass has already FOUND, so the line belongs with the money
    the books hold -- and reading only the gap put it in
    :attr:`Evidence.NOTHING_FOUND`, the one group that offers a sweep, under a
    heading saying nothing accounts for it.  Two adversarial reviews measured
    it independently 2026-08-28: one click filed a purchase into the envelope,
    which made the proposal impossible to accept and left the bank line it
    explained unexplained.

    Args:
        review: The pass, which owns both evidence signals.

    Returns:
        The rows, creatable then parked then inflows -- the grouping reorders
        them and the order inside a group is each list's own.
    """
    rows: "list[QueueRow]" = []
    for creatable in review.creatable:
        # **No counterpart fact is derived for an ordinary outflow** -- the
        # pass has one and it is an INFLOW's -- so a purchase the books
        # already hold in another shape reaches this queue with no positive
        # signal unless a rule withheld for it.  Finding **N-381**.
        rows.append(_row(review, creatable, QueueAct.RECORD_PURCHASE, None))
    for parked in review.parked:
        rows.append(_row(review, parked, QueueAct.NONE_OPEN, None))
    for inflow in review.recordable_inflows:
        # **Read ONCE and carried**, because it decides the group AND is
        # printed beside the line; asking again in the template would be the
        # redundant producer call this package refuses.
        rows.append(_row(
            review, inflow, QueueAct.RECORD_INCOME,
            review.income_already_recorded_in(inflow.line),
        ))
    return tuple(rows)


def _positive_for(
    item: "CreatableLine | ParkedLine | RecordableInflow",
    act: QueueAct, held: "IncomeAlreadyRecorded | None",
    gap: "str | None",
) -> bool:
    """Return whether a POSITIVE counterpart signal holds for one line.

    Args:
        item: The mechanism's value.
        act: Which control the row renders.
        held: The income this line's pay period already records, or ``None``.
        gap: This line's search gap, or ``None``.

    Returns:
        Whether something of the owner's already points at this line.  For a
        PARKED line the bar itself is that signal; for an INFLOW it is the
        money already recorded; for a CREATABLE line it is ANY withholding that
        is not the gap -- :data:`~._verdict._ALREADY_EXPLAINED`, or the
        double-count withholding plan step ``bank_import:X-gj-2b`` added for a
        refund.  **Read as a class, not enumerated**, which is what let the
        third arm arrive without a change here; the two are named because a
        reader deserves to know what reaches this line, not because the test is
        against a list.
    """
    if act is QueueAct.NONE_OPEN:
        return True
    if act is QueueAct.RECORD_INCOME:
        return held is not None
    return item.warning is not None and gap is None


def _row(
    review: "ReviewSet",
    item: "CreatableLine | ParkedLine | RecordableInflow",
    act: QueueAct, held: "IncomeAlreadyRecorded | None",
) -> QueueRow:
    """Return one assembled queue row.

    Args:
        review: The pass, which owns the search gap.
        item: The mechanism's value.
        act: Which control this row renders.
        held: What the books already record as unexplained income for this
            line's pay period, or ``None`` -- which for the two OUTFLOW
            mechanisms is always ``None``, since a bar and a deposit's books
            are different claims and no outflow has the second.

    Returns:
        Its :class:`QueueRow`.
    """
    # ONE ask per row, threaded to all three readers: the group, the
    # sentences, and the positive test each need the same answer.
    gap = review.search_gap_for(item.line)
    return QueueRow(
        evidence=_evidence_for(gap, _positive_for(item, act, held, gap)),
        item=item,
        act=act,
        notes=_notes_for(item, act, gap),
        income_already_held=held,
    )


def _sweeps_for(rows: "tuple[QueueRow, ...]") -> "tuple[QueueSweep, ...]":
    """Return the one-click controls this group's own lines support.

    **Counted where the sweep's rule is** (:attr:`~._placement.Placement
    .sweep_class`) and over THIS group's rows only, so a caption cannot promise
    a number the control does not deliver and the control cannot reach a line
    outside its own card.

    **There is no second guard against a WARNED line, and there must not be.**
    Only :attr:`Evidence.NOTHING_FOUND` is given sweeps, and for a creatable
    row that group is reached exactly when ``warning is None`` -- so
    :attr:`QueueRow.notes` is empty there BY CONSTRUCTION, and a
    ``or row.notes`` test here would be a condition no input could falsify.  A
    first fix added one; a mutation run measured that no test could kill it,
    which is this project's own definition of a fence.  What keeps the
    construction true is an assertion over the rendered set rather than a
    branch nothing reaches:
    ``TestNoSweptRowCarriesASentence`` fails the moment a withholding arm is
    added to :func:`~._verdict.ruled` without :func:`_positive_for` learning
    about it -- which is exactly how this was opened once.

    Args:
        rows: One group's rows.

    Returns:
        One :class:`QueueSweep` per class that has at least one line, in risk
        order.  A placement that is not an act -- a rule that does not reach
        this line's pay period -- has no class and is not counted.
    """
    counts: "dict[str, int]" = {}
    for row in rows:
        if not row.records_a_purchase:
            continue
        placement = row.item.placement
        css_class = (
            placement.sweep_class if placement is not None else None
        )
        if css_class is not None:
            counts[css_class] = counts.get(css_class, 0) + 1
    return tuple(
        QueueSweep(css_class=css_class, count=counts[css_class], label=label)
        for css_class, label in _SWEEP_LABELS if css_class in counts
    )


def statement_queue(review: "ReviewSet") -> StatementQueue:
    """Return the review queue as one list grouped by the decision each poses.

    Ruling **bank_import:R-HB**, plan step ``bank_import:X-gf-3b-2``.

    Args:
        review: The assembled pass (:class:`~._reads.ReviewSet`).  Taken whole
            rather than as three lists, because two of the evidence signals are
            METHODS over facts derived after the leftovers were split -- the
            rows this pass could not explain, and what each tier declined --
            and a caller re-deriving either could disagree with the surface the
            owner is sent to.

    Returns:
        The :class:`StatementQueue`, holding only the groups that have a row.
    """
    rows = _rows(review)
    groups = []
    for evidence in Evidence:
        mine = tuple(row for row in rows if row.evidence is evidence)
        if not mine:
            continue
        groups.append(QueueGroup(
            evidence=evidence,
            rows=mine,
            # **Only where recording is the indicated act** (developer ruling
            # 2026-08-28).  A bulk click may not reach a line whose own group
            # says the books may already hold it, or that the app could not
            # finish checking; those lines keep their own select and are
            # hand-picked.
            sweeps=(
                _sweeps_for(mine)
                if evidence is Evidence.NOTHING_FOUND else ()
            ),
        ))
    return StatementQueue(groups=tuple(groups))
