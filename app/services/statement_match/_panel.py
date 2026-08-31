"""What the OPENED card discloses: four verb tabs, and what each one offers.

Plan step ``bank_import:X-gj-1b``; rulings **bank_import:R-HR**, **R-HS**,
**R-HW** and **R-HX**.  Ruling **R-HR** draws the boundary this module exists
to state: *the card shows the DECISION; the disclosure is one click away*.  A
:class:`~._cards.LineCard` carries what is printed on the card -- the bank's
facts, the amount, one sentence and OK -- and a :class:`VerbPanel` carries
everything the panel behind it renders.

**It is two values rather than one wide one, and that is what deleted a
fence.**  :class:`~._cards.LineCard` carried twelve attributes under a
``too-many-instance-attributes`` disable, and plan step ``X-gj-1b`` needed two
more: which act ADD would perform for this line, and the rows its MATCH tab
offers.  Fourteen under a disable is a rule the linter states and nobody
enforces; the split states the same boundary in the type system, so a template
cannot print a panel fact beside the sentence and every value here is under
the ceiling on its own.

**Each verb tab is its own value where it has anything to offer.**  ADD files
a purchase into a container the owner picks between, or records an income row
against no container at all -- two doors, two controls -- so
:class:`AddTab` names WHICH, and no template asks a bank line's sign.  MATCH
offers rows, so :class:`MatchTab` carries them.  TRANSFER and SKIP have no
door in this build at all (:data:`~._verbs.TRANSFER_WAITS`,
:data:`~._verbs.SKIP_WAITS`), so they have no value: what they render is the
explanation their :class:`~._verbs.VerbOffer` already carries.

**The MATCH tab opens on the line's own pay period and the SEARCH reaches
further** (developer, 2026-08-30, on the measurement below).  Both halves are
load-bearing:

* the PERIOD is what the card renders with the page, so the group the owner
  actually builds is one click deep and needs no JavaScript.  Measured
  2026-08-30 on the developer's own account: 6 to 15 rows per card, 311 row
  renders across all 27 cards -- and every payroll deposit finds its own
  components there, ``2026-03-26``'s `$2,573.42` against
  ``Health Insurance Allowance`` `$100.00` + ``Data Manager`` `$2,473.38`, a
  difference of `$0.04` (finding **balance:N-391**, which is the bank half
  of the retired **N-239**);
* the SEARCH is over every unexplained row on the account, because a bound
  that cannot be widened is the cap finding **N-374** refused -- *the row that
  explains a line may be number 51*.  Measured on the same data: all 9 of the
  9 card payments have payback rows their own period does NOT hold, the worst
  ``2026-04-10``'s `-$627.39` whose period holds no payback row at all.
  Rendering every row in every card is what the widening replaces, and that
  was measured out: 67 rows in 18 cards at the workbench's own 991 bytes a
  row is ~1.2 MB.

**No bound of the PROPOSER's is applied here** (:func:`~._pairing
.within_window`).  That predicate answers *would the app OFFER this pairing*,
and this list answers *what may the owner ASSERT* -- the question the
hand-build workbench answers with no day test at all.  Narrowing by it would
withdraw from the owner a pairing the accept door would take, which is the
opposite of every bound this package applies.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import, no clock read, no query -- the pass
and the calendar arrive already derived.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._verbs import Verb, VerbOffer

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services import pay_calendar

    from ._creations import PurchaseDestination
    from ._offers import BankLine, CandidateRow, MatchProposal
    from ._placement import Placement
    from ._reads import ReviewSet
    from ._scope import ReviewScope


class AddAct(enum.Enum):
    """Which act the ADD verb performs for one line.

    Ruling **bank_import:R-HP** names ADD once -- *new spending or income the
    budget did not have* -- and the app has TWO doors under it:
    :func:`~._create.create_purchase_from_line` files a purchase into a
    container, and ``bank_import:R-GW``'s income door records a row against no
    container at all.  They take different submissions and offer different
    controls.

    **It is a FIELD the builder states rather than a sign the reader tests.**
    Every card builder already knows which of the pass's lists it drew its
    line from, so it reads the answer off that mechanism -- the argument
    :func:`~._verbs.offers_for` makes in as many words for ``add_waits``.  A
    template (or this module) asking ``line.amount > 0`` would be a SECOND
    spelling of :func:`~._leftovers._recordable_inflows`' own partition, and
    the two would not even agree: an outflow the bank dates as MADE after it
    POSTED reaches neither list (finding **N-325**).
    """

    PURCHASE = "purchase"
    INCOME = "income"


@dataclass(frozen=True)
class AddTab:
    """What the panel's ADD tab offers for one line.

    Attributes:
        act: Which act ADD performs here (:class:`AddAct`).
        destinations: The budget lines this could become a purchase against,
            in the line's own pay period.  **Empty is a real answer** -- a
            period whose every envelope has closed at a fixed figure offers
            none, and a NEW envelope is then the only arm -- and it is always
            empty for :attr:`AddAct.INCOME`, which is filed against no
            container.
        placement: What a standing rule comes to for this line
            (:class:`~._placement.Placement`), or ``None`` where the owner has
            stated none.  Ruling **R-HS**: a destination a rule NAMES is a
            suggestion the app can justify, so the control opens on it.

    **A card whose ADD has no act at all carries no** :class:`AddTab`: a
    proposal's line has no destination worked out for it, and a line ruling
    **R-GJ** bars may never become spending.  The tab still RENDERS on those
    cards (**R-HW**) -- what it renders is its
    :attr:`~._verbs.VerbOffer.waiting_for`, which is the whole of what a shut
    verb has to say.
    """

    act: AddAct
    destinations: "tuple[PurchaseDestination, ...]"
    placement: "Placement | None"

    @property
    def records_a_purchase(self) -> bool:
        """Return whether ADD would file a purchase into a container.

        Returns:
            Whether :attr:`act` is :attr:`AddAct.PURCHASE`.  **A predicate
            rather than a value for the template to compare**, so the screen
            never spells an enum's own string: the two predicates here and
            :attr:`records_income` partition :class:`AddAct`, and a template
            renders one arm each with no ``else`` -- so a third act added
            later renders nothing rather than the wrong control.
        """
        return self.act is AddAct.PURCHASE

    @property
    def records_income(self) -> bool:
        """Return whether ADD would record an income row against no container.

        Returns:
            Whether :attr:`act` is :attr:`AddAct.INCOME`.  See
            :attr:`records_a_purchase` for why this is a predicate.
        """
        return self.act is AddAct.INCOME


@dataclass(frozen=True)
class VerbPanel:
    """The opened card: all four verbs, and what each of them offers.

    Ruling **bank_import:R-HW**: all four render on every line whatever this
    build can act on, and a verb whose door does not exist renders its
    explanation and NO submitting control.

    Attributes:
        offers: All four verbs and whether each has a door
            (:func:`~._verbs.offers_for`), in :class:`~._verbs.Verb`'s own
            order, which is the order the tabs render.
        notes: Every sentence this pass owes the reader about this line -- a
            rule it withheld, a tier's refusal, a bar -- in reading order.
            **Here and NOT on the card** (**R-HR**): printing them beside the
            line is the grain that put two sentences on the review screen
            sixteen times.
        answer_door: The sentence naming where a standing answer that parks
            this line is changed, or ``None`` where changing it would change
            nothing (:attr:`~._bars.ParkedLine.answer_door`).
        add: What ADD offers (:class:`AddTab`), or ``None`` where this line's
            mechanism offers no ADD act at all.
        proposal: The match a tier offers for this line
            (:class:`~._offers.MatchProposal`), or ``None``.  Its rows arrive
            TICKED, which is ruling **R-HS**'s justified pre-fill.

            **It carried a ``MatchTab`` holding this plus the card's candidate
            ROWS until plan step ``bank_import:X-gj-1b``.**  Those rows were
            derived for all 248 of the developer's cards and rendered on none:
            the panel's row list is lazy-loaded, so the fragment asks
            :class:`MatchCandidates` for itself and the card never needed
            them.  Deleting the field left the tab a one-field wrapper, so the
            wrapper went too -- what MATCH offers that is a fact about the
            LINE is exactly this one thing, and the rest is a fact about the
            PASS that the pane reads directly.
    """

    offers: "tuple[VerbOffer, ...]"
    notes: "tuple[str, ...]"
    answer_door: "str | None"
    add: "AddTab | None"
    proposal: "MatchProposal | None"

    def offer_for(self, verb: Verb) -> VerbOffer:
        """Return this line's offer for one verb.

        Args:
            verb: Which of the four.

        Returns:
            Its :class:`~._verbs.VerbOffer`.  Total, because
            :func:`~._verbs.offers_for` emits all four for every line
            (**R-HW**) -- so this cannot answer ``None`` and no caller needs
            an absence arm.

        Raises:
            StopIteration: Never in practice, and deliberately not defended
                against: a panel missing a verb would mean
                :func:`~._verbs.offers_for` had stopped being total, which is
                a defect to see rather than to absorb.
        """
        return next(offer for offer in self.offers if offer.verb is verb)

    @property
    def open_verbs(self) -> "tuple[Verb, ...]":
        """Return the verbs this line may actually end on, in tab order.

        Returns:
            The verbs whose door would accept this line.  Empty is reachable:
            a parked card payment on an account whose every row is already
            explained has no act at all, which is the holding state ruling
            **R-HQ** names.
        """
        return tuple(
            offer.verb for offer in self.offers if offer.is_open
        )


@dataclass(frozen=True)
class MatchCandidates:
    """Every unexplained row of the pass, indexed for the MATCH tabs.

    **Derived ONCE for the whole page rather than per card.**  A card's list
    is its line's pay period, and 27 cards on the developer's own account
    resolve to 11 periods -- so asking the calendar per card would be the
    redundant producer call inside one request this package treats as a DRY
    violation rather than as a cost.

    Attributes:
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, which places a
            line.  Taken from the pass rather than loaded, so one request
            holds ONE calendar.
        by_period: The rows each pay period holds, keyed by its
            ``period_index`` -- the owner's own 0-based ordinal, which
            every derived period has.  **Not by ``period_id``**, which is
            ``None`` for every period past the saved horizon, so keying on
            it would collapse all of them onto one bucket.
        every: Every unexplained row, in the pass's own order -- what the
            search reaches.
    """

    calendar: "pay_calendar.PayCalendar"
    by_period: "dict[int, tuple[CandidateRow, ...]]"
    every: "tuple[CandidateRow, ...]"

    @classmethod
    def of(cls, scope: "ReviewScope", review: "ReviewSet") -> "MatchCandidates":
        """Return the pass's unexplained rows, indexed by pay period.

        **A row is in every period its own WINDOW touches**
        (:attr:`~._offers.CandidateRow.expected_window`), which is the one
        published answer to *when does the app think this money moved* and is
        deliberately not a second day rule invented here.  A purchase made in
        one period and settled in the next is therefore offered on both, which
        is right: either period's bank line may be the movement that settled
        it.

        Args:
            scope: The pass's scope, for the calendar.
            review: The pass.

        Returns:
            The :class:`MatchCandidates`.
        """
        by_period: "dict[int, list[CandidateRow]]" = {}
        for period in scope.calendar.periods:
            for row in review.unmatched_rows:
                window = row.expected_window
                # **A row the app can date no way at all reaches no period.**
                # It is unconstructible through either candidate arm -- both
                # fill ``expected_on`` from a NOT NULL column -- and
                # :func:`~._pairing.within_window` refuses the same row for
                # the same reason, so the two readers cannot disagree about
                # what an undatable row is worth.  The SEARCH still reaches
                # it, because a bound the owner cannot widen is the cap
                # finding **N-374** refused.
                if window is None:
                    continue
                if (
                    window[0] <= period.end_date
                    and window[1] >= period.start_date
                ):
                    by_period.setdefault(
                        period.period_index, []).append(row)
        return cls(
            calendar=scope.calendar,
            by_period={
                index: tuple(rows) for index, rows in by_period.items()
            },
            every=review.unmatched_rows,
        )

    def for_line(self, line: "BankLine") -> "tuple[CandidateRow, ...]":
        """Return the rows the card renders for one line, before any search.

        **The period is the one the bank POSTED the line into**, and that is
        the MATCH clock rather than the ADD clock: accepting a match writes
        the bank's POSTED day onto every row it names, where recording a
        purchase files it by the day it was MADE
        (:attr:`~._offers.BankLine.happened_on`).  The two clocks differ by
        ruling **R-FW** and each surface asks the one its own act turns on.

        Args:
            line: The bank line the card is about.

        Returns:
            That period's unexplained rows, or ``()`` when no saved period
            covers the posting day -- which the pass's own calendar split
            makes unreachable for a card, and is answered rather than raised
            so that a later reader cannot be surprised.
        """
        period = self.calendar.period_containing(line.posted_on)
        if period is None:
            return ()
        return self.by_period.get(period.period_index, ())

    def matching(self, query: str) -> "tuple[CandidateRow, ...]":
        """Return every unexplained row of the account matching *query*.

        **Over the whole account and never bounded**, which is the half of the
        developer's 2026-08-30 ruling that keeps a card payment groupable:
        finding **N-374** refused a cap on this list, and a search that could
        only reach one pay period would be one.

        **It matches the row's LABEL or its FIGURE**, because those are the
        two things an owner knows about a row they are looking for -- the
        `$2,473.38` of a payroll component as readily as its name.  The figure
        is compared as the plain decimal string the app stores, so no
        formatting decision is taken here: a service that searched
        ``"$2,473.38"`` would be the second money formatter this package
        refuses beside ``_money_macros.money``.

        Args:
            query: What the owner typed.  Empty (or blank) matches nothing,
                because an empty search is not a search -- the caller renders
                :meth:`for_line` instead.

        Returns:
            The matching rows, in the pass's own order.
        """
        wanted = query.strip().casefold()
        if not wanted:
            return ()
        return tuple(
            row for row in self.every
            if wanted in row.label.casefold()
            or wanted in f"{row.cash_amount:f}"
        )
