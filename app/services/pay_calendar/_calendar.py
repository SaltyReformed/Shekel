"""The pay calendar as ONE value, answering every "which paycheck" question.

Plan step **C2-a** (``docs/plans/implementation_plan_pay_calendar.md`` section
4), the first leaf of the step three arcs share -- it is also ``balance:X-l``
and ``recurrence:R-F12``.

**The first consumer arrived at plan step C2-d**, and until then nothing in
``app/`` called this at all -- that was C2-a's leaf boundary.  C1 built the
derivation and proved it byte-identical against production's 61 paydays before
anything read it; C2-a built the value and proved it the same way.  The cutover
leaves then move one consumer package each, and **C2-d took the first**: both
anchor-correction posting writers file every ledger entry's ``pay_period_id``
through :meth:`PayCalendar.filing_period`.  ``C2-b2`` (the recurrence engine),
``C2-c`` (the cash period view), ``C2-e`` (the projection axis) and ``C2-f``
(``pay_period_service``'s readers) remain.

Why the value exists at all: an AST census on 2026-08-10 found **SIX**
implementations of "which pay period contains this date" in ``app/`` -- ledger
row **P6**, which had claimed three until then -- and an adversarial review of
this step found a **SEVENTH** the same day (``savings_dashboard_service``'s
``_period_id_at``), which the census structurally could not see because it keyed
on the containment PREDICATE.  They disagree at exactly the edges that matter.
Two bisect and answer ``None`` outside the schedule; one scans linearly and
falls back past the end of it; one scans SYNTHETIC periods; two are SQL, and one
of those (``get_current_period``) has no ``ORDER BY`` at all (row **P19**).
Seven answers to one question is the defect; the number of QUESTIONS is three,
and they are named here so a caller has to choose:

===================================== ==================================
question                              method
===================================== ==================================
which SAVED paycheck covers this day  :meth:`PayCalendar.period_containing`
                                      -- ``None`` in a hole or outside,
                                      which is what the recurrence engine
                                      needs to tell a schedule hole from
                                      "the schedule has not reached there
                                      yet"
which span covers this day, saved or  :meth:`PayCalendar.span_containing`
projected                             -- TOTAL from the first payday on
which SAVED paycheck does a record    :meth:`PayCalendar.filing_period`
FILE under                            -- never ``None``; a foreign key
                                      points at the answer
===================================== ==================================

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
slice is a
:class:`PeriodWindow`, which is NOT a :class:`PayCalendar`, cannot be derived
from, and carries the ends the whole calendar computed.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Every answer is a pure function of the paydays and the cadence the
caller supplies.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from operator import attrgetter

from ._derive import DerivedPeriod, PayCalendarError, derive_periods

#: The bisect key for every search here: a period's opening payday.  Module
#: level so no two searches can key on different fields -- which is one of the
#: ways the six implementations row P6 counts came to disagree.
_BY_START_DATE = attrgetter("start_date")


def containing_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the period of *periods* whose span covers *day*, else ``None``.

    **The single containment search**, shared by :class:`PayCalendar` and
    :class:`PeriodWindow` so the calendar and a view over it cannot answer
    differently -- the whole point of plan step C2 being that six copies of
    this predicate already do.

    Periods never overlap (they are derived from a set of distinct sorted
    paydays), so the latest period STARTING on or before *day* is the only
    candidate that can contain it and one bisect answers.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The containing :class:`~._derive.DerivedPeriod`, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    period = periods[index]
    return period if day <= period.end_date else None


def latest_started_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the last period of *periods* opening on or before *day*, else ``None``.

    **The single ordering search**, shared by
    :meth:`PayCalendar.period_starting_on_or_before` and by
    :meth:`PayCalendar.filing_period` -- which needs it over the MATERIALISED
    subset rather than over every payday, and a second bisect written for that
    would be the duplication this step exists to remove.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The last period whose ``start_date`` is on or before *day*, or ``None``
        when *day* precedes every one of them.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    return periods[index]


def opening_payday(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the first payday of *periods*, or ``None`` when there are none.

    **The single opening-bound rule.**  Shared with the recurrence arc's
    ``PeriodCalendar``, which held a byte-identical copy until plan step C2-a --
    two implementations of "where does this schedule start", which is the defect
    row P6 counts on the containment question and this one has in miniature.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The earliest ``start_date``, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[0].start_date


def period_by_id(
    periods: "tuple[DerivedPeriod, ...]", period_id: "int | None",
) -> "DerivedPeriod | None":
    """Return the period of *periods* carrying *period_id*, else ``None``.

    **The single identity lookup.**  Shared with the recurrence arc's
    ``PeriodCalendar`` at plan step C2-b1 for the reason every other primitive
    here is shared: two implementations of one question drift, and this one
    answers a WRITE question -- which stored row a rule's authored start period
    names -- so a drift places a generated row against the wrong paycheck.

    Linear rather than a map built at construction, and deliberately: a
    calendar is built once per request and the lookup runs once per rule, so an
    index would be a second derived value to keep in step with :attr:`periods`
    for no measured gain (61 paydays against 46 live rules on production).

    Args:
        periods: The owner's periods, in any order.  Identity is not a search
            over a sorted key, so unlike the two bisects above this carries no
            ordering precondition.
        period_id: A ``budget.pay_periods.id``, or ``None``.

    Returns:
        The matching :class:`~._derive.DerivedPeriod`, or ``None`` when
        *period_id* is ``None`` or names no period here.  ``None`` in is
        ``None`` out rather than an error: a rule may legitimately name no
        start period, and the foreign key is ``ON DELETE SET NULL`` -- though a
        stale in-memory id can outlive the row it named, which is the second
        way this answers ``None``.  A PROJECTED period can never match, because
        every one of them carries ``period_id = None``.
    """
    if period_id is None:
        return None
    for period in periods:
        if period.period_id == period_id:
            return period
    return None


def earliest_start_in_month(
    periods: "tuple[DerivedPeriod, ...]", year: int, month: int,
) -> "date | None":
    """Return the earliest payday of *periods* falling in *year* / *month*.

    **The single "when does this month's first paycheck land" rule**, shared
    with the recurrence arc's ``PeriodCalendar`` at plan step C2-b1.  It is the
    one question ``Monthly First`` asks: that pattern fires on each month's
    FIRST paycheck, so whether a month can honour a rule depends on when its
    first paycheck arrives.

    A minimum over the periods that exist rather than an index into a walk,
    because months with no payday are legal -- a cadence longer than a month
    leaves some empty, and the schedule ends somewhere.

    Args:
        periods: The owner's periods, in any order.  It takes a minimum rather
            than a first match, so like :func:`period_by_id` and unlike the two
            bisects it carries no ordering precondition.
        year: Calendar year.
        month: Calendar month, 1-12.

    Returns:
        The earliest ``start_date`` in that month, or ``None`` when no period
        opens there.  ``None`` is a real answer, not an error.
    """
    starts = [
        period.start_date for period in periods
        if period.start_date.year == year and period.start_date.month == month
    ]
    if not starts:
        return None
    return min(starts)


def final_covered_day(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the last day *periods* covers, or ``None`` when there are none.

    The symmetric partner of :func:`opening_payday`, and shared for the same
    reason.  The LAST period's ``end_date`` rather than a maximum over all of
    them, because the periods are ordered and non-overlapping by construction.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The last covered day, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[-1].end_date


@dataclass(frozen=True)
class PeriodWindow:
    """A contiguous SLICE of one calendar, carrying the ends that calendar derived.

    **Not a calendar, and the type distinction is the fix for ledger row P14.**
    ``PeriodCalendar.from_pay_periods`` and ``_cash_periods._PeriodSpans.of``
    both accept an arbitrary list today, so deriving over a six-period grid
    window gives that window's last period a cadence-projected end while the
    same period ends a day earlier everywhere else.  A window cannot be derived
    FROM -- it is only ever produced by :meth:`PayCalendar.window`,
    :meth:`PayCalendar.overlapping` or :meth:`PayCalendar.axis`, each of which
    slices a calendar that was built from a complete payday set.

    Attributes:
        periods: The sliced periods, ``start_date`` ascending.  May be empty --
            a window over a range the calendar does not reach is a real answer,
            not an error.
    """

    periods: "tuple[DerivedPeriod, ...]"

    def containing(self, day: date) -> "DerivedPeriod | None":
        """Return the period of this window whose span covers *day*, else ``None``.

        Scoped to the window ON PURPOSE, and that is a question in its own
        right rather than a weaker form of the calendar's: the cash period
        view's reconciliation identity reads a period's balance change as the
        steps inside its own span, so a day outside the REPORTED columns
        belongs to no column and must not be pulled into the nearest one.

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`~._derive.DerivedPeriod`, or ``None`` when
            *day* falls outside every period in this window.
        """
        return containing_period(self.periods, day)

    def __iter__(self) -> "Iterator[DerivedPeriod]":
        """Iterate the window's periods in ``start_date`` order.

        Returns:
            An iterator over the periods.
        """
        return iter(self.periods)

    def __len__(self) -> int:
        """Return how many periods the window holds.

        Returns:
            The period count.
        """
        return len(self.periods)


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
    """

    user_id: int
    paydays: "tuple[tuple[int | None, date], ...]"
    cadence_days: "int | None"
    periods: "tuple[DerivedPeriod, ...]" = field(
        init=False, repr=False, compare=False,
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

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`~._derive.DerivedPeriod`, or ``None``.
        """
        return containing_period(self.periods, day)

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

        Returns:
            The covering :class:`~._derive.DerivedPeriod` -- saved when the
            schedule reaches *day*, projected when it does not -- or ``None``
            when the calendar is empty or *day* precedes
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
        saved = tuple(
            period for period in self.periods if period.period_id is not None
        )
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
        index = bisect_left(self.periods, day, key=_BY_START_DATE)
        if index >= len(self.periods):
            return None
        return self.periods[index]

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

    # ---- identity, and the month a paycheck opens --------------------

    def period_by_id(self, period_id: "int | None") -> "DerivedPeriod | None":
        """Return the SAVED period carrying *period_id*, else ``None``.

        The one question here that is not about a DATE: a recurrence rule names
        its start period by stored id, so resolving it is a lookup rather than
        a search.  Never answers a projected period, which has no id.

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
        which fabricates its own periods with ids numbered from 1 in the same
        integer namespace as real ``budget.pay_periods.id`` (ledger row
        **P17**) and at a hardcoded 14-day cadence that no call site overrides
        (row **P20**).  This projects at the OWNER's cadence, and a projected
        period says so with ``period_id = None``.

        Args:
            first_day: Inclusive lower bound of the range.
            last_day: Inclusive upper bound of the range.

        Returns:
            The covering periods as a :class:`PeriodWindow`, saved where the
            schedule reaches and projected beyond it.  Empty when the calendar
            is empty, or when the range ends before the owner's first payday --
            nothing is projected backwards.

        Raises:
            PayCalendarError: *last_day* precedes *first_day*.
        """
        saved = self.overlapping(first_day, last_day)
        horizon = self.horizon()
        if horizon is None or last_day <= horizon:
            return saved
        projected, period = [], self._projected_after(horizon + timedelta(days=1))
        while period.start_date <= last_day:
            if period.end_date >= first_day:
                projected.append(period)
            period = self._projected_after(period.end_date + timedelta(days=1))
        return PeriodWindow(periods=saved.periods + tuple(projected))

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
