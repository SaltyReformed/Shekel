"""The pay calendar as ONE value, answering every "which paycheck" question.

Plan step **C2-a** (``docs/plans/implementation_plan_pay_calendar.md`` section
4), the first leaf of the step three arcs share -- it is also ``balance:X-l``
and ``recurrence:R-F12``.

**The first consumer arrived at plan step C2-d**, and until then nothing in
``app/`` called this at all -- that was C2-a's leaf boundary.  C1 built the
derivation and proved it byte-identical against production's 61 paydays before
anything read it; C2-a built the value and proved it the same way.  The cutover
leaves then move one consumer package each.  **C2-d took the first**: both
anchor-correction posting writers file every ledger entry's ``pay_period_id``
through :meth:`PayCalendar.filing_period`.  **C2-b2 took the second**: the
recurrence engine resolves, walks and places against this value, and the
``PeriodCalendar`` it used -- which COPIED each period's stored end and ordinal
-- was deleted with the fences it carried.  **C2-c took the third**: the balance
seam's thirteen per-period entries stopped TAKING a period list and now read
:meth:`PayCalendar.saved` off their read pass, which deleted
``_cash_periods._PeriodSpans`` -- a fourth index over the STORED spans.
**TWO readers of the stored columns survived INSIDE that seam and an earlier
draft of this paragraph claimed none did** (adversarial review, 2026-08-13):
``balance_at/_cash_fold._cash_plan`` clamps a projected row against
``txn.pay_period``'s span -- it still does, and it is named in the pay-calendar
plan's section 3 as ``C4``'s -- and ``balance_at/_asset_contributions`` walked
``pay_period_service.get_all_periods``, whose ORDER is the stored ordinal and
whose order its year-to-date accumulation depends on.  **The second went at
``C2-f2a``** (ledger row **P37**): the contribution tier takes the read pass's
own calendar, so no module under ``balance_at`` IMPORTS ``pay_period_service``
and the ordering rule is the derivation rather than a sort at that door.
``C2-e`` (the projection axis) has since shipped; ``C2-f``'s remaining leaves
(``pay_period_service``'s readers at every surface outside this seam) have not.

Why the value exists at all: an AST census on 2026-08-10 found **SIX**
implementations of "which pay period contains this date" in ``app/`` -- ledger
row **P6**, which had claimed three until then -- and an adversarial review of
this step found a **SEVENTH** the same day (``savings_dashboard_service``'s
``_period_id_at``), which the census structurally could not see because it keyed
on the containment PREDICATE.  They disagree at exactly the edges that matter.
Two bisect and answer ``None`` outside the schedule; one scans linearly and
falls back past the end of it; one scans SYNTHETIC periods; two are SQL, and one
of those (``get_current_period``) has no ``ORDER BY`` at all (row **P19**).
Seven answers to one question is the defect; the number of CONTAINMENT
questions is three, and they are named here so a caller has to choose:

===================================== ==================================
question                              method
===================================== ==================================
which SAVED paycheck covers this day  :meth:`PayCalendar.period_containing`
                                      -- ``None`` only BEFORE the first
                                      payday or PAST the horizon; these
                                      periods tile, so a hole is not a
                                      third case (ledger row **P25**)
which span covers this day, saved or  :meth:`PayCalendar.span_containing`
projected                             -- TOTAL from the first payday on
which SAVED paycheck does a record    :meth:`PayCalendar.filing_period`
FILE under                            -- never ``None``; a foreign key
                                      points at the answer
===================================== ==================================

**Beside them sit FOUR ORDERING searches, which are a 2x2 rather than a list**,
and stating it as one is what keeps a caller from reaching for the wrong half:
the axis is BEFORE or AFTER a day, and the bound is INCLUSIVE or STRICT.

========== ============================================ ====================
axis       inclusive (the day itself qualifies)         strict (it does not)
========== ============================================ ====================
backwards  :meth:`period_starting_on_or_before`         :meth:`period_starting_before`
forwards   :meth:`period_starting_on_or_after`          :meth:`period_starting_after`
========== ============================================ ====================

**The strict pair arrived at plan step C2-f1** with the deletion of
``pay_period_service.get_next_period`` and ``companion_service.get_previous_period``,
which asked "the next / previous paycheck" as ``period_index +/- 1`` queries.
Reaching for an INCLUSIVE search to mean "the next one" answers with the SAME
period, and two of the callers write the answer into
``transactions.pay_period_id`` -- so the wrong pick files a credit-card payback
in the paycheck it is paying back.  The 2x2 is above the table for that reason,
and ``test_pay_calendar_value.TestPeriodStartingAfter`` pins all four corners.

**The filing rule was ruled a second QUESTION, not a compensator** (developer,
2026-08-10).  ``loan_ledger`` answers it today with a three-branch chain across
two functions, one of which is a public package export whose only caller sits
twelve lines below it.  Measured over 1,800 (shape, day) pairs of STORED-style
periods -- contiguous, gapped, two-hole, single-period, one-day and long-tail
-- that chain is exactly *"the latest period starting on or before the day,
else the earliest"*, which is
:meth:`PayCalendar.period_starting_on_or_before` clamped onto the saved set.
The gapped shapes live in that probe and not in this value's tests, because a
hole is unconstructible here: the periods tile.  So the rule is DERIVED from a
search rather than being a fourth scan, and the search itself is the missing
mirror of :meth:`PayCalendar.period_starting_on_or_after`, which the recurrence
arc's calendar already carried.

**Why the filing rule cannot simply be deleted, measured rather than argued.**
The competing option was to drop ``journal_entries.pay_period_id`` and derive a
ledger entry's paycheck from its ``entry_date``.  On ``shekel-prod-db``:
**14 days carry TWO different paychecks for ONE entry date**, so the date does
not determine the paycheck; **35 of 327 entries (10.7%)** are dated outside
their own paycheck across five of the seven source kinds, which is the budget
clock and the cash clock legitimately disagreeing; and **4 ``loan_opening``
entries** -- a mortgage dated 2018-12-01, a van loan 2023-02-14 -- precede the
owner's first payday (2026-03-26) by years and rely on the clamp to name any
paycheck at all.  Ledger row **P18** carries the full measurement; whether the
column stays is plan step ``C7``'s, and this method is what ``C7`` would delete
if it rules the column away.

**A WINDOW is a VIEW, and the type is what makes that structural** (ledger row
**P14**).  A period's end is its successor's payday, so deriving a calendar from
a SLICE gives that slice's last row a cadence-projected end instead of a
fact-dictated one -- the same period reporting two different ends depending on
which window asked.  The sibling shape is measured at ``$150,000.00``
(:func:`~._derive.derive_periods`, which holds it now: it was on
``loan_ledger/_visible.owner_pay_periods``, deleted at plan step C2-d).  Here a
slice is a :class:`~._window.PeriodWindow`, which is NOT a
:class:`PayCalendar` and which carries the ends the whole calendar computed.
What that type guarantees is that no CONSTRUCTOR accepts a window -- not that a
window cannot be taken apart into paydays, which it can, and which an earlier
draft of this paragraph claimed otherwise (ledger row **P24**).

**This module holds the CALENDAR and nothing else since plan step C2-c**, which
is what the 1,000-line ceiling was measuring: the shared SEARCHES moved to
:mod:`._searches` and the view type to :mod:`._window`, and the dependency runs
one way through all three.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Every answer is a pure function of the paydays and the cadence the
caller supplies.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from ._cadence import PayCadence
from ._derive import DerivedPeriod, PayCalendarError, derive_periods
from ._searches import (
    containing_period,
    earliest_started_period,
    earliest_start_in_month,
    final_covered_day,
    latest_started_period,
    materialised_periods,
    opening_payday,
    period_by_id,
)
from ._window import PeriodWindow


@dataclass(frozen=True)
class PayCalendar:
    """One owner's whole pay calendar, derived from their COMPLETE payday set.

    **The calendar stores the PAYDAYS and DERIVES the periods, which is the
    same normalization the whole arc is about, applied to the value itself.**
    :attr:`periods` is not an ``__init__`` field: it is computed once at
    construction and the value is frozen, so the fact and its derivation cannot
    drift -- there is no writer to reconcile.  That also makes the TILING
    invariant structural rather than checked.  Consecutive paydays define
    adjacent intervals, so ``[opening_bound(), horizon()]`` is covered with no
    hole and no overlap BY CONSTRUCTION, and the recurrence arc's
    ``PeriodCalendar.__post_init__`` -- which raises on an overlapping or
    reversed schedule -- has nothing left to refuse.  A fence deleted by making
    its subject unconstructible, not by trusting a caller.

    **The completeness of the payday set is the one precondition this value
    cannot check**, and saying so is the honest form of ledger row P14: a slice
    of paydays is indistinguishable from a short schedule.  What IS structural
    is that a subset is a :class:`PeriodWindow`, which is not a calendar and
    cannot be derived from, so the obligation lands in exactly one place --
    :meth:`from_paydays` -- and no consumer can rebuild a calendar out of a
    window it was handed.

    Attributes:
        user_id: The owner whose paydays these are.  Carried rather than
            inferred because a calendar is legal with no periods at all (a
            companion holds none by design, and production has one such user),
            so there is no row to read it off.  It is what lets a consumer
            refuse to resolve one owner's rule against another owner's
            schedule -- a pairing that produces a plausible wrong answer rather
            than an error.
        paydays: The owner's COMPLETE payday set as ``(period_id, payday)``
            pairs.  The only fact here; everything else is derived from it and
            :attr:`cadence_days`.
        cadence_days: Days between paydays, from ``budget.pay_schedule``.  Read
            for the last saved period's end and for every projected period past
            it; validated by :func:`~._derive.derive_periods`.
            **``None`` ONLY when :attr:`paydays` is empty** (plan step C2-b1),
            and that pairing is enforced rather than documented: an owner with
            no schedule row and no period to infer one from has no last period,
            so the value is provably unread, while the same absence beside a
            payday is plan finding **P8**'s broken state and is refused at
            construction.  Every method that reads it is reachable only from a
            non-empty calendar, so none of them tests it.
        periods: The owner's SAVED periods, ``start_date`` ascending.  DERIVED
            at construction, never passed in, and excluded from equality so two
            calendars compare on their facts.
        _saved: :meth:`saved`'s memo, filled on first use and excluded from
            equality for the same reason.  A one-element list because a frozen
            dataclass cannot rebind a field.
    """

    user_id: int
    paydays: "tuple[tuple[int | None, date], ...]"
    cadence_days: "int | None"
    periods: "tuple[DerivedPeriod, ...]" = field(
        init=False, repr=False, compare=False,
    )
    _saved: "list[PeriodWindow]" = field(
        init=False, default_factory=list, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        """Derive the periods from the paydays, and CANONICALISE the paydays.

        ``object.__setattr__`` because the dataclass is frozen: both values are
        written exactly here, at construction, and nothing can rewrite either
        afterwards -- which is what makes :attr:`periods` a cache with a
        structural reconciler rather than the stored-derived-value defect this
        arc exists to remove.

        **:attr:`paydays` is rewritten FROM the derived periods, and that is
        not circular -- it is the fix for an ordering leak.**  A caller may
        hand over paydays in any order, so without this two calendars built
        from the same SET in different orders hold different tuples and compare
        unequal while every answer they give is identical: one value with two
        representations, which is the shape this whole arc removes.  Reading
        the canonical form back off the derivation means the ordering rule is
        stated once, in :func:`~._derive.derive_periods`, instead of twice.

        Raises:
            PayCalendarError: Anything :func:`~._derive.derive_periods` refuses
                -- a bad cadence, a payday that is not a plain ``date``, an id
                that is neither ``int`` nor ``None``, or a repeated payday.
        """
        periods = derive_periods(self.paydays, self.cadence_days)
        object.__setattr__(self, "periods", periods)
        object.__setattr__(
            self,
            "paydays",
            tuple((period.period_id, period.start_date) for period in periods),
        )

    @classmethod
    def from_paydays(
        cls,
        paydays: "Iterable[tuple[int | None, date]]",
        cadence_days: "int | None",
        user_id: int,
    ) -> "PayCalendar":
        """Build a calendar from an owner's COMPLETE payday set.

        Args:
            paydays: Every payday the owner has, as ``(period_id, payday)``
                pairs in any order.  ``period_id`` is the
                ``budget.pay_periods.id`` the payday was read from; ``None``
                marks a period no foreign key can point at.  **The whole set,
                never a window** -- see the class docstring.
            cadence_days: Days between paydays, from ``budget.pay_schedule``,
                or ``None`` for an owner who has neither a schedule row nor a
                period to infer one from.  ``None`` beside a non-empty
                *paydays* is refused.
            user_id: The owner these paydays belong to.

        Returns:
            The frozen calendar.

        Raises:
            PayCalendarError: Anything :func:`~._derive.derive_periods` refuses.
        """
        return cls(
            user_id=user_id,
            paydays=tuple(paydays),
            cadence_days=cadence_days,
        )

    # ---- how often this owner is paid --------------------------------

    @property
    def cadence(self) -> PayCadence:
        """Return how often this owner is paid, as a value of its own.

        **So that a caller already holding a calendar never builds a second
        answer** (plan step R7a-2a).  :class:`~._cadence.PayCadence` is the one
        producer of "how many paychecks in a year" and of the unit conversions
        that rest on it; this is the door for the consumers that have a whole
        calendar in hand -- the Recurring surface, the recurrence write paths --
        while :func:`~._loader.cadence_for` serves the ones that need the
        cadence and nothing else.  Both answer from :attr:`cadence_days`, so
        there is one fact and one derivation however it is reached.

        Returns:
            The owner's :class:`~._cadence.PayCadence`.

        Raises:
            PayCalendarError: This calendar holds no cadence, which
                :attr:`cadence_days` documents as possible ONLY for an empty
                calendar.  An owner with no paydays and no schedule row has
                never stated how often they are paid, and every monthly
                equivalent on every page is a function of that -- so there is
                nothing to answer with.  Refused rather than defaulted for the
                reason :func:`app.services.recurrence._resolution._effective_start`
                refuses the same owner: a broken invariant rather than a state
                to paper over, and a silently assumed biweekly rhythm would
                render a weekly-paid owner every figure at half its true value.
                Unreachable through a registered owner since plan step X-ad-a,
                which made registration write the ``budget.pay_schedule`` row.
        """
        if self.cadence_days is None:
            raise PayCalendarError(
                f"user {self.user_id} has no pay cadence, so how many "
                f"paychecks they receive in a year is unanswerable.  Their "
                f"calendar holds {len(self.periods)} payday(s) and no "
                f"budget.pay_schedule row to read a cadence from; since plan "
                f"step X-ad-a registration writes one, so this is legacy or "
                f"companion data rather than a state to default.  Assuming "
                f"biweekly would report a weekly-paid owner's commitments at "
                f"half their true monthly value."
            )
        return PayCadence(cadence_days=self.cadence_days)

    # ---- the schedule's own bounds -----------------------------------

    def opening_bound(self) -> "date | None":
        """Return the owner's first payday, or ``None`` for an empty calendar.

        The day below which :meth:`span_containing` stops answering.  Nothing
        is projected backwards past it: before an owner's first payday there is
        no paycheck, and inventing one would attribute money to a paycheck that
        never happened.  :meth:`filing_period` is the one rule that must answer
        there anyway, and it CLAMPS rather than projecting.

        Returns:
            The earliest ``start_date``, or ``None`` when the calendar is empty.
        """
        return opening_payday(self.periods)

    def horizon(self) -> "date | None":
        """Return the last day the SAVED schedule covers, or ``None`` when empty.

        Saved, deliberately, and the reason is NARROWER than the one an earlier
        draft gave (ledger row **P25**).  That draft said the recurrence engine
        needs this bound to tell a schedule HOLE from "the schedule has not
        reached there yet"; on a DERIVED calendar the periods tile, so there is
        no hole to tell apart and that reason has expired.  What has not: a
        horizon that moved with the projection would let generation seat a row
        in a period with no ``id`` for ``transactions.pay_period_id`` to point
        at.  The bound is about the foreign key, not about holes.

        Returns:
            The last saved period's ``end_date``, or ``None`` when empty.
        """
        return final_covered_day(self.periods)

    # ---- the three questions -----------------------------------------

    def period_containing(self, day: date) -> "DerivedPeriod | None":
        """Return the SAVED period whose span covers *day*, else ``None``.

        ``None`` is a real answer and not an error, and there are exactly TWO
        ways to get it here: *day* precedes the first payday, or it lies past
        the horizon.  **A hole is NOT one of them** -- these periods are derived
        from the paydays and therefore TILE, which is ledger row **P25** and
        which an earlier draft of this docstring got wrong by carrying the
        stored model's third case forward.  Answering ``None`` outside the
        covered span rather than clamping to the nearest period is the point:
        seating a bill in a paycheck whose span does not contain it silently
        misplaces real money.

        **SAVED is ENFORCED rather than described, since plan step C2-f2b** --
        the last of the five searches here to rest on "no calendar holding an
        UNSAVED candidate reaches it".  That was true, and this package has
        twice ruled true is not structural: :meth:`filing_period` and
        :meth:`period_starting_after` were each corrected after a review fed
        them a candidate and got ``period_id=None`` back for a ``NOT NULL``
        column.  Both consumers here write that column -- the recurrence engine
        PLACES a row on this answer, ``companion_service`` SCOPES its query by
        it -- and ``== None`` is ``IS NULL``, which returns no rows silently.

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`~._derive.DerivedPeriod`, whose ``period_id``
            is never ``None``, or ``None`` when no saved period covers *day*.
        """
        return containing_period(materialised_periods(self.periods), day)

    def span_containing(self, day: date) -> "DerivedPeriod | None":
        """Return the span covering *day*, projecting past the horizon.

        **The TOTAL answer, and the reason this step exists** (``balance:X-l``,
        "the pay calendar answers any date").  Today ``get_all_periods``
        returns the saved rows and nothing else, so past the last payday every
        consumer improvises and the improvisations disagree: the modelled
        replay's accrual tier keeps running while its contribution tier stops
        (``+$2,501.92`` and ``+$5,427.07`` at six months out, ledger rows
        **N-82** / **P7**), and ``growth_engine`` invents its own axis at a
        hardcoded 14-day cadence (row **P20**).

        A projected period carries ``period_id = None`` -- the marker C1 built
        for exactly this -- so a caller that needs a foreign key target cannot
        mistake one for a saved row.  Its ``period_index`` continues the saved
        sequence, and ``end_is_projected`` is ``True``.

        Answers ``None`` BEFORE the opening bound rather than projecting
        backwards: the ruling of 2026-08-10 is forward projection, because a
        day before the owner's first payday genuinely has no paycheck, and the
        one caller that needs an answer there is the FILING rule, which clamps
        (:meth:`filing_period`).

        Args:
            day: The calendar day to place.

        Args:
            day: The calendar day to place.

        Returns:
            The covering :class:`~._derive.DerivedPeriod` -- MATERIALISED when
            the schedule reaches *day* (:meth:`period_containing` answers that
            half and states its own filter), projected when it does not -- or
            ``None`` when the calendar is empty or *day* precedes
            :meth:`opening_bound`.
        """
        opening = self.opening_bound()
        if opening is None or day < opening:
            return None
        saved = self.period_containing(day)
        # No third branch, and its absence is load-bearing.  A first cut had
        # one for "inside the saved span but in a hole"; the periods TILE
        # ``[opening_bound(), horizon()]`` by construction, so that branch was
        # unreachable -- dead code carrying a claim about a state this value
        # cannot hold.  Past the horizon there is no saved period, and the
        # projection answers.
        return saved if saved is not None else self._projected_after(day)

    def filing_period(self, day: date) -> DerivedPeriod:
        """Return the SAVED period a record dated *day* files under.

        **A different question from containment, ruled so 2026-08-10**, because
        it may never answer ``None``: ``journal_entries.pay_period_id`` is a
        ``NOT NULL`` foreign key, so a ledger entry needs a paycheck a key can
        point at even when its own date lies outside every paycheck.  Four
        entries on production do -- a mortgage dated 2018-12-01 and a van loan
        2023-02-14, both years before the owner's first payday.

        The rule is ONE clamp: **the latest period starting on or before *day*,
        else the earliest**.  It replaced the three-branch chain
        ``loan_ledger.find_period_containing_date`` composed with
        ``resolve_anchor_pay_period`` -- containment, else the latest period
        ENDING before the day, else the earliest -- which plan step **C2-d**
        DELETED once both anchor-posting writers took this method.

        **The equivalence to that chain has a PRECONDITION, and the first
        statement of it here named only half.**  The two rules agree on every
        schedule that is non-overlapping AND whose ``period_index`` order
        matches its ``start_date`` order.  Drop the second half and they part
        company on 800 of 872 probed days: the old chain reduced by INDEX (its
        fallbacks took ``max(period_index)`` and its last resort ``periods[0]``)
        while this reduces by DATE.  Both halves were held by
        ``pay_period_service`` -- the batch guard ``_reject_overlapping_batch``
        and the tail-append at ``max_index + 1`` -- until plan step **C3-b**
        moved the writer to ``pay_period_write``, where neither survives as
        stated: an ordinal is no longer ASSIGNED at all, it is read off the
        derivation, so it cannot disagree with date order.  Where the two DO
        differ this one is
        right: it searches a calendar whose index is DERIVED from date order, so
        no stored ordinal can disagree with its own dates.

        The proofs, each saying what it covers:
        ``tests/test_services/test_pay_calendar_value.py`` grades this method
        against a transcription of the chain over every shape a
        :class:`PayCalendar` can HOLD, and separately over stored-style period
        lists a calendar cannot express -- gapped, two-hole, overlapping, and
        the index-order counterexample -- because derived periods TILE and so
        cannot produce a hole to test with;
        ``tests/manual/verify_filing_cutover.py`` re-runs both halves against a
        real database and is where the cutover's production numbers come from.

        **It never reads a period's END, and that is why C2-d could ship ahead
        of the two steps that close the calendar's write doors.**  The other
        four C2 cutovers wait on ``balance:X-ad`` and ``C3`` because a DERIVED
        calendar absorbs a hole instead of reporting it (ledger row **P27**);
        this one bisects on ``start_date`` through
        :func:`latest_started_period` and so cannot tell a derived end from a
        stored one.  A hole changes which period a day is INSIDE; it does not
        change which period most recently OPENED.  *The METHOD is end-free; the
        composition a caller reaches it through is not, and saying only the
        first is how the exemption gets over-read.*
        :func:`~._loader.calendar_for` resolves the cadence through
        ``pay_schedule_service.resolve_cadence``, which for an owner with no
        ``budget.pay_schedule`` row INFERS it from the last period's stored
        length -- plan finding **P8**.  *This sentence read "and the state
        every freshly-registered owner is in" until plan step X-ad-a, which
        made registration write the schedule row; the inference now serves
        only owners created before that step.*  It moves no filing answer
        (only the last period's derived end, which this never reads), but it
        does mean a value outside 1..365 would REFUSE the calendar rather than
        mis-file a record.

        **Always a MATERIALISED period**, and that is enforced rather than
        assumed.  A first cut searched all of :attr:`periods` and claimed the
        answer was saved because a projection is not in that tuple -- true of a
        projection and FALSE of an unsaved payday, which
        :func:`~._derive.derive_periods` accepts by design (``period_id`` is
        documented as ``None`` for "a candidate the writer has not saved yet",
        and plan step C3 makes the writer build exactly such a calendar).  An
        adversarial review of this step reproduced it: two lines of input, and
        the method returned a period whose id was ``None`` straight into a
        ``NOT NULL`` column.  It now searches the materialised periods only.

        Args:
            day: The date the record asserts or was posted at.

        Returns:
            The materialised :class:`~._derive.DerivedPeriod` the record files
            under.  Its ``period_id`` is never ``None``.

        Raises:
            PayCalendarError: The calendar holds no MATERIALISED period -- it
                is empty, or every payday in it is an unsaved candidate.  A
                loud refusal rather than ``None``: the caller is about to write
                a ``NOT NULL`` column, and there is no safe value to invent.
        """
        saved = materialised_periods(self.periods)
        if not saved:
            raise PayCalendarError(
                f"user {self.user_id} has no materialised pay period, so a "
                f"record dated {day.isoformat()} has no paycheck to file "
                f"under.  journal_entries.pay_period_id is NOT NULL and this "
                f"calendar holds {len(self.periods)} payday(s), none of them "
                f"saved, so there is no id to point at -- a broken invariant "
                f"rather than an input this can clamp."
            )
        located = latest_started_period(saved, day)
        return located if located is not None else saved[0]

    # ---- the two ordering searches -----------------------------------

    def period_starting_on_or_after(self, day: date) -> "DerivedPeriod | None":
        """Return the first SAVED period opening on or after *day*, else ``None``.

        "The NEXT paycheck."  One half of the recurrence placement axis
        (:class:`~app.enums.PeriodPlacementEnum`
        ``PERIOD_STARTING_ON_OR_AFTER``): a ``Monthly First`` rule puts its row
        in the first paycheck on or after the 1st of the month.

        Args:
            day: The calendar day to place.

        Returns:
            The first period whose ``start_date`` is on or after *day*, or
            ``None`` when the schedule reaches no such period -- a day past the
            horizon, where the answer is "not yet" rather than "never".
        """
        return earliest_started_period(self.periods, day)

    def period_starting_on_or_before(self, day: date) -> "DerivedPeriod | None":
        """Return the last SAVED period opening on or before *day*, else ``None``.

        "The MOST RECENT paycheck."  The exact mirror of
        :meth:`period_starting_on_or_after`, and the half that was missing --
        three modules had open-coded it, one of them as a scan with two
        fallbacks (ledger row **P6**).  :meth:`filing_period` is this search
        plus one clamp.

        Distinct from :meth:`period_containing` only in a HOLE and past the
        horizon, which is exactly where the six implementations P6 counts
        disagreed: containment answers ``None`` there, this answers the period
        that opened last.

        Args:
            day: The calendar day to place.

        Returns:
            The last period whose ``start_date`` is on or before *day*, or
            ``None`` when *day* precedes every payday.
        """
        return latest_started_period(self.periods, day)

    def period_starting_after(self, day: date) -> "DerivedPeriod | None":
        """Return the first MATERIALISED period opening STRICTLY after *day*.

        "The NEXT paycheck after this one", asked by passing that one's own
        payday.  The strict sibling of :meth:`period_starting_on_or_after`,
        and it exists as a method for the reason that search does: plan step
        **C2-f** retired ``pay_period_service.get_next_period`` -- a query on
        ``period_index + 1`` -- and "add a day, then search from there" is an
        off-by-one each of its callers would otherwise carry a copy of.  A copy
        that got it wrong would answer with the SAME paycheck, which for the
        two credit-payback writers means a payback landing in the period it is
        paying back.  **FOUR ``app/`` call sites, not the five the retired
        reader had**: the companion view's two collapsed into one
        ``_period_neighbours``, which asks this and its mirror together.

        Equal to the ordinal query it replaces by construction: this calendar's
        ``period_index`` IS payday order, so "the next index" and "the next
        payday" cannot name different periods -- which is the disagreement
        ``uq_pay_periods_user_index`` and three runtime fences exist to police
        on the stored columns.

        **MATERIALISED, and that filter is what makes the answer safe to write
        into a foreign key** -- the same enforcement :meth:`filing_period`
        carries, taken here for the same reason and put in before shipping by
        an adversarial review of C2-f1.  Two of the four callers write this
        period's id into ``transactions.pay_period_id``, which is ``NOT NULL``,
        and :attr:`~._derive.DerivedPeriod.period_id` is nullable in general.
        A projection is NOT the risk (this search never projects); the risk is
        the OTHER way a period is unmaterialised -- an unsaved candidate, which
        :func:`~._derive.derive_periods` accepts by design and which plan step
        C3's writer builds.  A first cut searched all of :attr:`periods` and
        argued in a docstring that no such calendar reaches these callers;
        that argument was TRUE and is not a property, which is the distinction
        :meth:`filing_period` was corrected on one step earlier.  Skipping a
        candidate is also the right answer on its own terms: a caller asking
        for the next paycheck wants one a record can point at.

        Args:
            day: The payday to search forward from, itself excluded.

        Returns:
            The next materialised period -- its ``period_id`` is never
            ``None`` -- or ``None`` when *day* falls in or after the last saved
            period, which is "the schedule has not reached there yet" and which
            every caller answers rather than projecting past.
        """
        return earliest_started_period(
            materialised_periods(self.periods), day + timedelta(days=1),
        )

    def period_starting_before(self, day: date) -> "DerivedPeriod | None":
        """Return the last MATERIALISED period opening STRICTLY before *day*.

        "The PREVIOUS paycheck", the exact mirror of
        :meth:`period_starting_after` and the half ``companion_service`` held
        as its own ``period_index - 1`` query until plan step **C2-f**.  Both
        halves of the companion view's period navigation ask one value now, so
        stepping back and then forward cannot land somewhere else -- a property
        two independent ordinal queries never had, and one
        ``test_pay_calendar_value`` asserts across the whole schedule.

        MATERIALISED for the reason :meth:`period_starting_after` gives, and
        symmetrically rather than because this side writes a foreign key: its
        one caller builds a URL from the id, and a link to ``/companion/period/None``
        is the same defect one register down.

        Args:
            day: The payday to search backward from, itself excluded.

        Returns:
            The previous materialised period -- its ``period_id`` is never
            ``None`` -- or ``None`` when *day* is at or before the owner's
            first payday.
        """
        return latest_started_period(
            materialised_periods(self.periods), day - timedelta(days=1),
        )

    # ---- identity, and the month a paycheck opens --------------------

    def period_by_id(self, period_id: "int | None") -> "DerivedPeriod | None":
        """Return the SAVED period carrying *period_id*, else ``None``.

        The one question here that is not about a DATE.

        **It has FIVE live ``app/`` callers, and this paragraph claimed ZERO
        until 2026-08-16.**  Plan step R7b-4's note -- "nothing in the
        application asks this" -- was already false then (``grid/partials.py``,
        ``companion_service.py``), and pay-calendar plan step C2-f2d-3 added the
        salary cockpit's period selector, its anatomy fragment and the
        recurrence engine's pricing lookup.  **A sentence about the tree goes
        stale exactly like one about the code**; this method is load-bearing.

        Never answers a projected period, which has no id.

        Args:
            period_id: A ``budget.pay_periods.id``, or ``None``.

        Returns:
            The matching :class:`~._derive.DerivedPeriod`, or ``None`` -- see
            :func:`period_by_id` for the two distinct ways that happens.
        """
        return period_by_id(self.periods, period_id)

    def earliest_start_in_month(self, year: int, month: int) -> "date | None":
        """Return the earliest payday falling in *year* / *month*, else ``None``.

        What ``Monthly First`` asks: that pattern fires on a month's FIRST
        paycheck, so honouring it depends on when that paycheck lands.

        Args:
            year: Calendar year.
            month: Calendar month, 1-12.

        Returns:
            The earliest ``start_date`` in that month, or ``None`` when the
            SAVED schedule opens no period there -- a month a long cadence
            skips, or one past the horizon.
        """
        return earliest_start_in_month(self.periods, year, month)

    # ---- views, never calendars --------------------------------------

    def saved(self) -> PeriodWindow:
        """Return every MATERIALISED period of this calendar as one window.

        **The balance seam's whole reporting domain** (plan step C2-c).  Every
        per-period entry the seam publishes -- the grid's column set, the cash
        map, the kind-correct balance maps, the loan map -- answers over the
        owner's entire saved schedule, and each of them used to TAKE that set
        as an argument every one of its eight callers filled with the same
        value.  An argument a caller can get wrong is a defect rather than a
        contract, so the argument is gone and this is what replaced it; the
        seam reads it once per read pass through
        :meth:`~app.services.balance_at.BalanceContext.reported_periods`.

        MATERIALISED, and that filter is load-bearing rather than defensive:
        the seam's maps are keyed by ``budget.pay_periods.id``, so an
        unmaterialised period would key every one of them under ``None`` and
        collapse them onto each other (ledger row **P21**'s shape).  The two
        ways a period can be unmaterialised are named at
        :func:`materialised_periods`; neither reaches a calendar built by
        :func:`~._loader.calendar_for`, which reads saved rows only.

        Projections are NOT here, and that is the same distinction
        :meth:`period_containing` draws against :meth:`span_containing`: a
        balance column needs a row a ``transactions.pay_period_id`` can point
        at, and the forward projection past the horizon is
        :meth:`axis`'s answer to a different question.

        **Memoized on the calendar rather than on its caller**, because this
        is where the derivation lives: the balance seam asks for it once per
        ACCOUNT (``build_maps`` over nine accounts ran the filter, the sort and
        the contiguity scan nine times for one answer), and a memo on the read
        pass would have been a memo of a memo.  The slot is a one-element list
        because the dataclass is frozen; it is excluded from equality, so two
        calendars still compare on their facts.  A RAISING build is not cached,
        so the refusal below fires on every call rather than once.

        Returns:
            The :class:`PeriodWindow` over every saved period, ``start_date``
            ascending.  Empty for a calendar with no saved period -- an owner
            who has never generated a schedule, and the companion role.

        Raises:
            PayCalendarError: The saved periods do not cover an unbroken span,
                which means an UNSAVED candidate sits between two saved ones.
                Unreachable through :func:`~._loader.calendar_for` (it reads
                only saved rows) and through ``pay_period_write`` (it appends
                candidates after the last saved payday); it is refused rather
                than reported over, because a hole in the reported set is a
                balance column that does not add up.
        """
        if not self._saved:
            self._saved.append(
                PeriodWindow(periods=materialised_periods(self.periods)),
            )
        return self._saved[0]

    def window(self, first_index: int, count: int) -> PeriodWindow:
        """Return *count* SAVED periods from ordinal *first_index* onward.

        The grid's six-period window and every other index-keyed slice.  A
        :class:`PeriodWindow`, not a calendar: the periods carry the ends this
        calendar derived from the whole payday set, which is what row **P14**
        needs and what deriving over the slice would destroy.

        Args:
            first_index: The first ``period_index`` to include.
            count: How many periods to take.  A non-positive count yields an
                empty window rather than an error -- "no periods requested" is
                a legal request.

        Returns:
            The :class:`PeriodWindow`, shorter than *count* when the calendar
            ends first and empty when *first_index* is past the end.
        """
        if count <= 0:
            return PeriodWindow(periods=())
        return PeriodWindow(
            periods=tuple(
                period for period in self.periods
                if first_index <= period.period_index < first_index + count
            ),
        )

    def overlapping(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return every SAVED period overlapping ``[first_day, last_day]``.

        A period overlaps when ``start_date <= last_day`` and
        ``end_date >= first_day``; both bounds are inclusive.  The calendar-
        month and calendar-year slices the reporting surfaces ask for.

        Args:
            first_day: Inclusive lower bound of the range.
            last_day: Inclusive upper bound of the range.

        Returns:
            The overlapping periods as a :class:`PeriodWindow`, empty when none
            overlaps.

        Raises:
            PayCalendarError: *last_day* precedes *first_day*, which is a
                caller that has its bounds crossed rather than a range that
                happens to be empty -- the two are indistinguishable in the
                result and only one is a defect.
        """
        if last_day < first_day:
            raise PayCalendarError(
                f"overlapping() was asked for {first_day.isoformat()}.."
                f"{last_day.isoformat()}, which ends before it starts.  An "
                f"empty range and a crossed one both return no periods, so "
                f"the crossed one is refused rather than answered."
            )
        return PeriodWindow(
            periods=tuple(
                period for period in self.periods
                if period.start_date <= last_day
                and period.end_date >= first_day
            ),
        )

    def axis(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return the spans covering ``[first_day, last_day]``, projecting past the horizon.

        **The replacement for ``growth_engine.generate_projection_periods``**,
        DELETED at plan step **C2-e**, which fabricated its own periods with ids
        numbered from 1 in the same integer namespace as real
        ``budget.pay_periods.id`` (ledger row **P17**) and at a hardcoded
        14-day cadence that no call site overrode -- so an owner paid monthly
        was credited ``365/14`` paycheck contributions a year and shown
        ``$1,300,344.92`` against a true ``$711,385.70`` (row **P20**).  This
        projects at the OWNER's cadence, and a projected period says so with
        ``period_id = None``.

        **It COVERS the range it is given or it refuses; it never covers part
        of one** (ledger row **P23**, ruled 2026-08-14 by the developer).  A
        range opening below :meth:`opening_bound` used to come back silently
        short -- ``axis(2025-12-20, 2026-03-01)`` on a calendar opening
        2026-01-02 left 13 days in no period -- and a short axis is
        indistinguishable from a complete one in the result.  That is the
        argument :meth:`overlapping` already makes for refusing a CROSSED
        range, applied to the other end.  Nothing is projected backwards
        (ruling 2026-08-10: before an owner's first payday there is no
        paycheck), so covering such a range is not an option and refusing is
        the only honest answer left.

        **The clamp a live consumer needs is its own method**, and deliberately
        not a branch in here: :meth:`projection_axis` raises *first_day* to
        :meth:`opening_bound` for a pass that opens before the owner's first
        payday -- an ordinary state, since the Generate form asks for "your
        next (or first) payday".  The pairing is the one
        :meth:`filing_period` already makes against
        :meth:`period_starting_on_or_before`: the strict search answers or does
        not, and the TOTAL companion beside it states its clamp in the open
        where a reader and a test can both see it.  Every projecting surface
        calls the companion, so **no caller in ``app/`` can reach the refusal
        below** -- it guards the value against a caller assembled by hand, the
        same standing :meth:`overlapping`'s crossed-range refusal has.

        An EMPTY calendar is not that case and answers an empty window: an
        owner with no paydays at all has no partial coverage to hide, which is
        the same answer :meth:`saved` gives them.

        Args:
            first_day: Inclusive lower bound of the range.  Must be at or after
                :meth:`opening_bound` unless the calendar is empty.
            last_day: Inclusive upper bound of the range.

        Returns:
            The covering periods as a :class:`PeriodWindow`, saved where the
            schedule reaches and projected beyond it.  Empty only for an empty
            calendar -- for any other calendar the range is covered in full.

        Raises:
            PayCalendarError: *last_day* precedes *first_day*, or *first_day*
                precedes this calendar's :meth:`opening_bound`.
        """
        saved = self.overlapping(first_day, last_day)
        opening = self.opening_bound()
        if opening is not None and first_day < opening:
            raise PayCalendarError(
                f"axis() was asked for {first_day.isoformat()}.."
                f"{last_day.isoformat()}, which opens before user "
                f"{self.user_id}'s first payday ({opening.isoformat()}).  "
                f"Nothing is projected backwards -- before the first payday "
                f"there is no paycheck -- so the {(opening - first_day).days} "
                f"day(s) below it can only be left out, and an axis that "
                f"silently covers part of its range reads exactly like one "
                f"that covers all of it.  Call projection_axis() if raising "
                f"the range's start to the opening bound is what was meant."
            )
        horizon = self.horizon()
        if horizon is None or last_day <= horizon:
            return saved
        projected, period = [], self._projected_after(horizon + timedelta(days=1))
        while period.start_date <= last_day:
            if period.end_date >= first_day:
                projected.append(period)
            period = self._projected_after(period.end_date + timedelta(days=1))
        return PeriodWindow(periods=saved.periods + tuple(projected))

    def projection_axis(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return the paychecks a FORWARD projection over ``[first_day, last_day]`` runs on.

        :meth:`axis` with ONE clamp, and the same pairing :meth:`filing_period`
        makes against :meth:`period_starting_on_or_before` (plan step C2-e).
        The strict search refuses a range it can only half-cover; this is the
        TOTAL companion every projecting surface actually calls, and it states
        its clamp in the open rather than absorbing it.

        **The clamp exists for the owner whose first payday has not happened
        yet**, which is an ordinary state rather than a broken one: the
        Generate form asks for "your next (or first) payday", so a read pass
        whose ``as_of`` precedes the whole schedule is what a new owner looks
        like on the day they set it up.  Three surfaces resolve a projection
        axis -- /retirement with its two lever solvers, /savings Horizon, and
        the /investment growth chart -- and each raising *first_day* itself
        would be three copies of one rule, the fourth of which a later consumer
        forgets.

        **Nothing is clamped at the other end**, and the asymmetry is the
        point.  A *last_day* past the schedule's horizon is exactly what
        :meth:`axis` projects for, at the owner's own cadence; a *first_day*
        below the opening bound is a span no paycheck ever covered.

        **A CROSSED range is still refused**, and telling it apart from an
        emptied one is why the two tests below are separate (adversarial code
        review, 2026-08-14).  A caller whose bounds are the wrong way round is
        a defect, and folding it into the empty answer is the hole
        :meth:`overlapping` refuses to leave open one level down.  A range the
        CLAMP empties is a different thing entirely -- a horizon already behind
        the owner's first payday -- and is a real answer: it is the /retirement
        lever page's ``past_horizon`` state, where a shortfall exists but no
        paycheck remains for new money to land in.

        Args:
            first_day: The day the projection window opens -- the day AFTER the
                balance it seeds from is valued.  Raised to :meth:`opening_bound`
                when it precedes it.
            last_day: The last day the projection reaches.  May lie past the
                schedule's horizon, which is what the projection is for.

        Returns:
            The :class:`~._window.PeriodWindow` covering the (possibly raised)
            range, saved where the schedule reaches and projected at the
            owner's cadence beyond it.  **Empty** when this calendar holds no
            payday, and when the raised range would end before it starts.

        Raises:
            PayCalendarError: *last_day* precedes *first_day* as the caller
                supplied them.
        """
        if last_day < first_day:
            return self.axis(first_day, last_day)
        opening = self.opening_bound()
        if opening is None:
            return PeriodWindow(periods=())
        window_opens = max(first_day, opening)
        if last_day < window_opens:
            return PeriodWindow(periods=())
        return self.axis(window_opens, last_day)

    def _projected_after(self, day: date) -> DerivedPeriod:
        """Return the projected period covering *day*, past the saved horizon.

        Projection is arithmetic on the LAST SAVED payday rather than a walk:
        paydays continue at :attr:`cadence_days`, so the period covering *day*
        is the ``n``-th one after the last saved payday where ``n`` is the
        whole number of cadences between them.  Computing it directly means the
        cost does not grow with how far past the horizon a caller asks.

        Every projected period reports ``end_is_projected`` ``True``, which
        stays faithful to C1's meaning of that flag: the end comes from the
        cadence rather than from a payday anyone has recorded.

        Args:
            day: A calendar day strictly after :meth:`horizon`.  The caller
                guarantees it -- both callers reach here only after testing,
                which also guarantees :attr:`cadence_days` is an ``int`` here:
                a day past the horizon means the calendar has one, and a
                calendar with a period cannot have been constructed without a
                cadence (:func:`~._derive.derive_periods` refuses that pair).

        Returns:
            The projected :class:`~._derive.DerivedPeriod`, carrying
            ``period_id = None`` and a ``period_index`` continuing the saved
            sequence.
        """
        last = self.periods[-1]
        elapsed = (day - last.start_date).days
        steps = elapsed // self.cadence_days
        start = last.start_date + timedelta(days=steps * self.cadence_days)
        return DerivedPeriod(
            period_id=None,
            period_index=last.period_index + steps,
            start_date=start,
            end_date=start + timedelta(days=self.cadence_days - 1),
            end_is_projected=True,
        )
