"""What an unexplained bank line may BECOME, and where it would land.

Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1`` split this out of
:mod:`._reads`.  **The seam is the SUBJECT**, which is the argument
:mod:`._section` already makes one module over: that one answers *what does the
review screen SHOW about this pass*, and this answers *what may one line the
matcher could not explain be turned into*, which is a different question with a
different grain -- one answer per line, and every one of them a door.

**Three answers, and a FOURTH class that has none** -- which is what this
module being one module makes visible, and a first draft of this header claimed
the three were total and was measured FALSE by this step's own adversarial
review:

* an outflow becomes a PURCHASE against a budget line the owner picks
  (:class:`CreatableLine`, ruling **R-FX**);
* an outflow whose merchant is barred becomes nothing, and is PARKED with the
  reason (:class:`~._bars.ParkedLine`, ruling **R-GJ**);
* an inflow becomes an uncategorized INCOME row
  (:class:`RecordableInflow`, ruling **bank_import:R-GW**);
* **an outflow the bank dates MADE after it POSTED reaches none of the three.**
  :func:`_creatable_lines` drops it before the split, on finding **N-325**'s
  developer ruling of 2026-08-19 -- *reported rather than repaired*, because
  the alternative decides which day the app believes when the bank contradicts
  itself, which ruling **R-FW** refused one clock over.  It survives only as
  :attr:`~._reads.ReviewBounds.impossible_day_count`, a number naming no line,
  and it is still counted by ``awaiting_review_count``.  **That is the same
  shape bank_import:R-GW closed for inflows**, on a class whose remedy is already ruled and
  already owned, so it is named here rather than repaired here: 0 of the
  developer's 378 recorded lines are it, and the OFX adapter's own measurement
  found 2 of 361.

**The third was missing until 2026-08-27 and the gap was invisible because the
three lived apart.**  ``_creatable_lines`` took ``amount < 0`` and nothing took
the other side, so eight of the developer's own deposits -- `$58.87`, five
dividends and three card refunds -- had no act on the review screen at all,
while the two refusals that should have caught it pointed at each other.  A
module whose whole subject is *what may this line become* is where a missing
direction is one function short rather than nowhere -- and the fourth class
above is the proof that it works: it was invisible while these lived apart and
is a bullet here.

**The split is a line cap made useful rather than worked around**, exactly as
:mod:`._creations` and :mod:`._section` record: adding the inflow arm took
:mod:`._reads` past this project's 1,000-line module bound, and the two honest
answers are to cut the record or to cut the module.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read.  It READS and never
writes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

from app.services.status_seam import day_is_in_the_future

from ._bars import CreationBars, ParkedLine
from ._creations import PurchaseDestination, envelope_answer_key
from ._offers import BankLine
from ._placement import Placement, placements_for
from ._rules import RuleView
from ._scope import ReviewScope, no_period_refusal
from ._section import MerchantSection, merchant_section

if TYPE_CHECKING:  # pragma: no cover -- the edge back would be a cycle
    # :mod:`._verdict` imports THIS module, so the annotation is a forward
    # reference and the import is type-checking only.  The direction is
    # right: a line is built before the pass that rules on it exists.
    from ._verdict import RuleVerdict


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
            line (:class:`~._placement.Placement`), or ``None`` when they have not
            said where this merchant goes -- which is a different answer from
            "they said never" and the screen says it differently.  Plan step
            ``bank_import:X-f6a-3d``.
            **It is a SUGGESTION and never a tick**: the destination select
            still opens on *leave this line alone*, and what turns a placement
            into an act is the owner pressing the sweep.  A remembered
            destination that arrived already selected would be a default
            pointing at money, which is what ruling **R-FZ** removed.
        verdict: What ruling **R-GH**'s automatic door would do about this line
            (:class:`~._verdict.RuleVerdict`), or ``None`` where no stated rule
            names a destination for its merchant.  Set by
            :func:`~._verdict.ruled` after the pass exists, exactly as
            :attr:`~._placement.Placement.joins_new` is set by
            :func:`_marked_joining` -- the facts it rests on belong to the PASS
            and are not known when this value is built.
        withheld: Why the create door would REFUSE this line, or ``None``
            when it would not.  **Symmetric with**
            :attr:`RecordableInflow.withheld` **and asked of the same
            predicate**: no saved pay period covers the day the purchase was
            made, so there is no budget for it to belong to.  It is a
            different fact from :attr:`warning`, which explains why the line
            is still here; this says the ADD control may not be rendered at
            all.
        warning: The whole sentence the screen prints beside this line, or
            ``None``.  **Composed in the service and printed unbranched**
            (finding **N-359**, plan step ``bank_import:X-gf-3a``): it is the
            partition *why is this line still here* -- a rule the pass
            withheld, or a search it did not finish -- and a template picking
            between those two with ``{% if %}``/``{% elif %}`` is the second
            place for it to be wrong that :attr:`~._bars.ParkedLine.reason`
            and :attr:`RecordableInflow.withheld` both exist to refuse.
            **A WIDER set than** :attr:`verdict`: a line no rule reaches can
            still be one the pass never finished looking at.
    """

    line: BankLine
    pay_period_id: "int | None"
    destinations: "tuple[PurchaseDestination, ...]"
    placement: Placement | None = None
    verdict: "RuleVerdict | None" = None
    warning: "str | None" = None
    withheld: "str | None" = None


@dataclass(frozen=True)
class RecordableInflow:
    """One unmatched bank line of money COMING IN, and where it would land.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  :class:`CreatableLine`
    without its two chooser fields, and the absence is the whole difference: a
    purchase is filed against a container the owner picks between, and an
    income row is filed against nothing -- so there are no destinations to
    offer and no placement to suggest, and a value carrying empty versions of
    both would be a control one Jinja condition away from rendering.  That is
    the argument :class:`~._bars.ParkedLine` already makes beside them.

    Attributes:
        line: The bank's own record of the movement.
        pay_period_id: The period covering the day the bank POSTED it, or
            ``None`` when no SAVED period does.  **The posting day and not the
            transaction day**, which is the residual's rule rather than the
            purchase's: this row has no budget clock of its own because it IS
            the movement (:meth:`~._scope.ReviewScope.period_holding`).
        withheld: Why this line may NOT be recorded, or ``None`` when it may.
            **ONE server-derived sentence rather than two Jinja conditions**,
            which is :attr:`~._bars.ParkedLine.reason`'s argument: a template
            restating a partition is a second place for it to be wrong, and
            here the two arms are *your calendar does not reach that day* and
            *your bank dates that money as moving in the future*.

            **The second arm is the one an adversarial review measured
            2026-08-27**, and it was a control whose submission could never
            succeed -- the shape this package had by then closed five times and
            this value's own docstring claimed to close a sixth.  Pay periods
            project about two years forward, so a future-dated line resolved a
            period, rendered a tick, and was refused by
            ``status_seam.reject_future_settle_day`` only AFTER the door had
            written and settled the row.  The screen and the door now ask ONE
            published predicate (:func:`~app.services.status_seam
            .day_is_in_the_future`) and the door asks it before it writes.
    """

    line: BankLine
    pay_period_id: "int | None"
    withheld: "str | None" = None


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
        by_period.setdefault(
            destination.period.period_id, [],
        ).append(destination)
    creatable: "list[CreatableLine]" = []
    parked: "list[ParkedLine]" = []
    for line in offerable:
        # ONE ask of the bar per line, and its answer is what routes the line:
        # a second ask -- once to partition and once to word the sentence -- is
        # the redundant producer call this module already refuses above.
        barred_by = bars.bar_for(line.merchant_id)
        if barred_by is not None:
            # **BOTH facts, because both bars can hold** (plan step
            # ``bank_import:X-gf-3a``): which one the line is parked BY decides
            # what the screen says happened, and whether the OTHER also holds
            # decides whether the owner has a door -- an answer they gave can
            # be given again, and a source's filing is lifted by nothing.  Both
            # come off the bars this pass already derived; the value asks
            # nothing for itself.
            parked.append(ParkedLine(
                line=line,
                barred_by=barred_by,
                also_pays_an_account=bars.pays_an_account(line.merchant_id),
            ))
            continue
        creatable.append(_one_creatable(
            line, _period_id_for(calendar, line.happened_on), by_period, view,
        ))
    return _marked_joining(creatable), tuple(parked), len(impossible)


def _recordable_inflows(
    calendar, unmatched: "list[BankLine]",
) -> "tuple[RecordableInflow, ...]":
    """Return the unmatched lines of money COMING IN, each placed by its day.

    Ruling **bank_import:R-GW**, plan step ``bank_import:X-gf-1``.  :func:`_creatable_lines`
    takes the outflows and this takes the other side, which is what closed a
    whole DIRECTION of movement having no act.

    **The two SIGNS are total and the two LISTS are not**, and the difference
    is the module header's fourth bullet.  ``ck_bank_statement_lines_amount_
    real_nonzero`` declares ``amount <> 0``, so ``amount < 0`` and
    ``amount > 0`` name every line the database can hold; but
    :func:`_creatable_lines` then drops the outflows whose bank dates the
    purchase after the posting (finding **N-325**), and those reach no list at
    all.  Stating the sign half alone is what this step's own adversarial
    review measured FALSE in a first draft of this docstring.

    **No bar is asked** (ruling **R-GJ**).  A bar says this merchant's money
    was SPENT somewhere the budget already holds, which is a claim about an
    outflow; a deposit is not spending, and neither of the two bars has an arm
    that could be true of one.

    Args:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places each
            line.  Taken rather than loaded, so one request holds ONE calendar.
        unmatched: The bank lines inside the calendar no proposal explains.

    Returns:
        One :class:`RecordableInflow` per inflow, in the order the lines were
        given.
    """
    return tuple(
        _one_inflow(line, _period_id_for(calendar, line.posted_on))
        for line in unmatched if line.amount > 0
    )


def _one_inflow(
    line: BankLine, pay_period_id: "int | None",
) -> RecordableInflow:
    """Return one inflow, and why the door would refuse it if it would.

    **Both arms are the DOOR's own, asked here so the control is not rendered
    for a line it will refuse** -- and the second is asked through the status
    seam's published predicate rather than by a second comparison, because a
    money rule spelled twice is this project's own root cause.

    Args:
        line: The unexplained inflow.
        pay_period_id: The period covering the day it POSTED, or ``None``.

    Returns:
        Its :class:`RecordableInflow`.
    """
    if day_is_in_the_future(line.posted_on):
        withheld = (
            f"Your bank dates this as arriving on {line.posted_on}, which has "
            f"not happened yet.  A row records the day its money moved, so "
            f"there is nothing to record until then."
        )
    elif pay_period_id is None:
        withheld = no_period_refusal(line.posted_on, "this income row")
    else:
        withheld = None
    return RecordableInflow(
        # **The POSTING day**, which is the day this row would settle on and
        # the day its period is resolved from -- one derivation, so the screen
        # cannot offer a control the door then refuses.
        line=line, pay_period_id=pay_period_id, withheld=withheld,
    )


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
        Its :class:`CreatableLine`.  A line no saved period covers gets no
        placement AND a :attr:`~CreatableLine.withheld` refusal, because a rule
        resolves into a destination and there is no period here for one to be
        in.  **Withholding the placement alone was not enough** (adversarial
        review 2026-08-29): the line stayed in ``creatable``, so a reader
        taking membership of that list as the door's answer offered a control
        ``ReviewScope.period_holding`` then refused by name -- the
        chooser-that-cannot-succeed shape, on any swipe made just before the
        first payday or posted past the last saved period.
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
        # **The MADE day**, which is the day this purchase would be placed by
        # and the day ``period_id`` was resolved from -- one derivation, so
        # the screen cannot offer a control the door refuses.
        withheld=(
            None if period_id is not None
            else no_period_refusal(line.happened_on, "this purchase")
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


@dataclass(frozen=True)
class Leftovers:
    """What this pass could not explain, placed against the owner's rule.

    Four facts one derivation produces that travel together, which is the
    argument :class:`~._offers.Candidates` and
    :class:`~._propose.ProposedMatches` already make in this package: a caller
    holding the offerable lines without the count of the ones declined would
    render a list that reads as complete.

    It leaves this module for :func:`~._reads.review_set`, which is what
    assembles it into a :class:`~._reads.ReviewSet`.

    Attributes:
        creatable: The offerable unexplained outflows a create control may be
            rendered for, each with its placement.
        parked: The offerable unexplained outflows ruling **R-GJ** bars, each
            with the reason (:class:`~._bars.ParkedLine`).
        recordable_inflows: The unexplained INFLOWS, each with the period
            that would hold it (ruling **bank_import:R-GW**).
        merchants: The rule control's rows and option list.
        impossible_day_count: How many outflows were declined for being dated
            MADE after they POSTED (finding **N-325**).
    """

    creatable: "tuple[CreatableLine, ...]"
    parked: "tuple[ParkedLine, ...]"
    recordable_inflows: "tuple[RecordableInflow, ...]"
    merchants: MerchantSection
    impossible_day_count: int


def leftovers(
    scope: ReviewScope,
    unmatched: "list[BankLine]",
    destinations: "list[PurchaseDestination]",
) -> "Leftovers":
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
        The :class:`Leftovers`.
    """
    view = RuleView.build(scope.owner_id, scope.account_id)
    bars = CreationBars.build(
        scope.owner_id, scope.account_id, rules=view.rules,
    )
    creatable, parked, impossible_days = _creatable_lines(
        scope.calendar, unmatched, destinations, view, bars,
    )
    return Leftovers(
        creatable=creatable,
        parked=parked,
        recordable_inflows=_recordable_inflows(scope.calendar, unmatched),
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
