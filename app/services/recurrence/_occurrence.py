"""
Shekel Budget App -- The forward occurrence engine (plan step R3)

Generation, stated forward.  :func:`occurrences` walks a rule's own cadence
from its first occurrence; :func:`place` carries one occurrence DATE onto the
pay period the row lives in; :func:`occurrence_placements` composes the two.

**This is what generation is.**  Plan step R3 built it parallel and unread;
plan step R4a made ``recurrence_engine.match_periods`` a thin adapter over it,
so every pay period the application generates a row into is selected here; and
plan step R4b-2 deleted that adapter, so every reader now takes the
``(occurrence, period)`` pairs themselves through
:func:`~app.services.recurrence.rule_occurrences`.

Why forward, and what that fixed
--------------------------------

Selection used to be a REVERSE mapping: it scanned every candidate pay
period and asked whether that period contained the rule's target day, through
five near-identical ``_match_*`` helpers.  Each of them inspected only the
months of a period's two ENDPOINTS, so a period spanning more than two months
could not match the interior ones -- and ``cadence_days`` is user-selectable
1..365 (``schemas/validation/pay_periods.py``), so that is reachable
configuration.  Measured on the R1 baseline's own 90-day schedule: a monthly
day-1 rule over three years matched 13 periods instead of 36 (and returned one
period TWICE, which would violate
``idx_transactions_template_period_scenario``), and a quarterly rule matched 1
of its 12 occurrences.  That was plan defect **D3**.

Generating forward and then placing removes the defect structurally rather
than by widening the scan: every occurrence the cadence names is emitted, at
every cadence, and a date the schedule cannot host is an explicit "no period"
rather than an occurrence that was never looked for.

Why the schedule is threaded in (finding **D9**)
------------------------------------------------

The plan's stated signature was ``occurrences(rule, window)``.  It cannot
serve the ``PERIOD`` unit: those occurrences are PAYCHECKS, and which dates
are paydays is a property of the owner's schedule, not of the rule.  So the
calendar is an argument, and it is what makes the function total over
:class:`~app.enums.RecurrenceUnitEnum` rather than over three of its four
members.

What an occurrence IS, per unit
-------------------------------

* ``MONTH`` / ``YEAR`` -- the anchor's day, in every *interval_n*-th month
  (or every *interval_n*-th YEAR, which is the same walk with a step of
  twelve), month-end clamped exactly as
  ``app.services.recurrence.resolve`` clamped the anchor itself.  The day
  clamped from is :attr:`ResolvedRecurrence.nominal_day` when the anchor month
  was too short to hold it, and ``anchor_date.day`` otherwise (ruling R-R3) --
  which is what keeps a day-31 rule on the last day of every month instead of
  decaying to the 30th forever.
* ``WEEK`` -- the anchor plus multiples of ``7 * interval_n`` days.  No
  pattern resolves to this unit yet; plan step R8 is its first author.  It is
  implemented here rather than refused because a partial function over an
  enum is the defect this redesign exists to remove, and because a walk that
  silently ignored the unit would be a wrong answer rather than an error.
* ``PERIOD`` -- **the qualifying paycheck's own payday.**  Here the anchor is
  a BOUND, not the first occurrence: ruling R-R8 settled that a period-space
  rule anchors on the effective start ITSELF, because "the first period ending
  on or after the bound" is not derivable when the bound falls past the
  materialised horizon.  The first occurrence is therefore the payday of the
  first paycheck that has not already ENDED before that bound -- which is what
  the reverse matcher selected (``p.end_date >= effective_from``), and it
  is deliberate: a loan whose first installment falls mid-period bills in that
  period, not the next (plan step C9a).  Ledger row **D6** records the same
  asymmetry from the schema's side.

**A consequence worth stating rather than discovering: placement is INERT
under the ``PERIOD`` unit.**  Every occurrence it emits is a period's own
``start_date``, and both placements carry such a date back to that same period
-- ``CONTAINING_DATE`` because the period contains its own opening day, and
``PERIOD_STARTING_ON_OR_AFTER`` because no earlier period starts later.  The
plan's section 3 says the opposite ("a mid-period bound places differently
under the two placements"); that claim reads the anchor as the emitted
occurrence, which the paragraph above is exactly the decision not to do.
Emitting the payday is what reproduced the reverse matcher's date as well as
its period: ``compute_due_date`` returns ``period.start_date`` for a rule with
no ``day_of_month``, so a mid-period bound emitted verbatim would have moved
the first row's date.

Bounds are OCCURRENCE bounds (ruling R-R6)
------------------------------------------

``end_date`` and ``max_occurrences`` are applied to the occurrence, not to the
period it lands in.  The reverse matcher bounded PERIODS -- ``end_date`` was
tested against a period's START -- so it generated rows dated outside the
window the user stated: measured on the R1 baseline, a monthly-15th rule
ending 2025-06-05 generated a row due 2025-06-15.  That was plan defect
**D5**, and it died here: an occurrence past ``end_date`` is simply never
emitted.

``max_occurrences`` counts OCCURRENCES the cadence names, including any the
schedule does not reach and never places.  "Stop after twelve" is a property
of the rule, not of how many rows the schedule happened to host.

The window, and the ONE answer ``period=None`` now gives (finding **D7**)
-------------------------------------------------------------------------

**It used to be two answers, and plan step C2-b2 collapsed them to one by
making the second unconstructible.**  A pay period's last covered day is now
DERIVED -- it is the day before the next payday
(:func:`app.services.pay_calendar.derive_periods`) -- so consecutive paydays
define adjacent intervals and the schedule TILES
``[opening_bound(), horizon()]`` with no hole and no overlap.  A day inside the
covered span therefore always has a period, and the SCHEDULE GAP that ledger
row D7 described stopped being a state a READER can see.  What was
policing it went with it: the ``SCHEDULE_GAP`` outcome, ``GenerationPlan.gaps``
and ``_recurrence_common.report_schedule_gaps`` (plan steps **C2-b2** /
**C5a**, which is recurrence **R-F10**).

So ``period is None`` means exactly one thing: the SAVED schedule does not
reach this occurrence.  That is ordinary rather than alarming, and on a
perfectly healthy schedule it happens constantly -- under
``PERIOD_STARTING_ON_OR_AFTER`` an occurrence dated after the LAST PAYDAY has
no paycheck to defer onto even though
:meth:`~app.services.pay_calendar.PayCalendar.period_containing` finds the day
covered, which is roughly one biweekly schedule in three (measured: any
schedule whose last period straddles a month boundary).  The next schedule
extend places it.

**Where the DERIVED calendar and the STORED columns disagree, this engine now
believes the derivation** -- and that MOVES MONEY.  Stated here rather than
discovered, because it is the whole risk surface plan step C2-b2 opened
(adversarial review, 2026-08-11, which measured all three shapes).  There are
three, and none of them is reachable through a live door: ``pay_period_write``
materialises the derivation over the whole payday list on every write, so each
one means rows written before plan step **C3-b** or edited outside that module,
and the owner's next payday write REPAIRS them.

* **A HOLE is absorbed** (plan ledger row **P27**).  A stored ``end_date``
  short of the next payday leaves days uncovered; the derivation runs the
  preceding paycheck to the day before that payday, so an occurrence there
  seats against a real period id and generates a row where it used to be
  logged and skipped.
* **The stored CADENCE moves the horizon** (row **P28**).  The last period's
  derived end is ``payday + cadence_days - 1``, so a stored cadence that no
  longer matches the stored end moves the generation window -- SHORTER loses
  the occurrences past the new horizon, LONGER seats rows in a paycheck whose
  stored span ends before their date.
* **A stored ORDINAL is re-derived** (row **P26**).  ``period_index`` becomes a
  period's position in payday order, so a stored ordinal that is not
  ``0..n-1`` re-phases every ``Every N Periods`` rule -- including one naming a
  start period, which the plan's first statement of P26 did not cover.

Two consequences ride on the first two.  Where the change puts a SECOND
occurrence of one template into one paycheck,
``_recurrence_common.refuse_unstorable_repeats`` refuses the whole pass -- the
same refusal a 30-day-or-longer cadence already earns, and plan step C5b is
what lifts it.  Where it does not repeat, the row is generated with a date
``compute_due_date`` reads off the paycheck's two ENDPOINT months rather than
off the occurrence this module found, so it can be dated in the wrong month
entirely -- plan ledger row **D18**, whose fix is recurrence plan step **R5**
(it gives the occurrence its own column and deletes ``compute_due_date``).
Both are measured and pinned by
``test_recurrence_engine.TestALegacyScheduleHole``.  Of the three shapes only
the HOLE has a detector: ``scripts/integrity_check.py`` **BA-07** asks it as a
query over the stored column and dies with that column at plan step C4.

:func:`occurrence_placements` generates through the schedule's HORIZON by
default, because that is the last day a placement can succeed at all.  A
caller that passes a later ``through`` gets those occurrences back explicitly,
each carrying ``period=None``.

Several occurrences in ONE paycheck, and why they are not collapsed
-------------------------------------------------------------------

**The condition is "a calendar month the schedule puts no payday in", which
begins at a 30-day cadence** -- not "a cadence longer than a month", which an
earlier draft of this docstring said and which is too narrow by a factor of
three.  It reaches both placements, differently:

* under ``CONTAINING_DATE`` a monthly bill at a 90-day cadence simply occurs
  three times inside one paycheck's span;
* under ``PERIOD_STARTING_ON_OR_AFTER`` -- today's ``Monthly First`` -- three
  months' occurrences DEFER onto the one paycheck that follows them.

Both are real obligations.  A monthly bill is owed monthly whatever the pay
cadence, and the reverse matcher walked PAYCHECKS instead of months, so it
emitted one row per paycheck and silently dropped the rest: 12 rows for 36
months of rent, measured on the R1 baseline's own 90-day schedule.  That was
defect D3, and for ``Monthly First`` it went unmeasured until plan step R3
added the missing oracle shapes.

``budget.transactions`` cannot hold them yet:
``idx_transactions_template_period_scenario`` is UNIQUE over
``(template, period, scenario)``.  **That index is keyed on the wrong column**
-- it is a generation-idempotency guard, and a generated row's identity is its
OCCURRENCE, not its paycheck.  Re-keying it onto ``(template, scenario,
occurs_on)`` is plan step R5's work, in the same migration that renames
``due_date`` to ``occurs_on`` and so first gives the occurrence a column.

**The rows stay separate; only the grid sums them** (developer ruling,
2026-08-07).  Summing at generation would fit today's index, and that is
exactly why it is tempting -- it buys the fit by destroying which month a
payment covers, by freezing an amount that may change mid-group, and by
putting one row in front of several events.  So the pairs are returned as
generated: this module answers "what does the cadence name", and presentation
is the display layer's.

A third placement -- "the LAST paycheck on or before the occurrence" -- is
what a bill funded IN ADVANCE needs, and the axis does not have it: today's
two values both fund on or after, so rent due the 1st is funded from a
paycheck that may fall after it.  Plan step R8 adds it.

Pure: no Flask, no ORM, no clock, no database.
"""
import calendar as calendar_module
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._months import (
    MONTHS_PER_YEAR,
    month_ordinal,
    walk_months,
)
from app.services.recurrence._resolution import ResolvedRecurrence

#: Days in a week, for the ``WEEK`` unit's stride.
_DAYS_PER_WEEK = 7


class RecurrenceGenerationError(ShekelError):
    """A resolved recurrence names something this engine cannot generate.

    A broken invariant rather than bad user input, and deliberately NOT
    :class:`~app.services.recurrence.RecurrenceResolutionError`: nothing
    failed to resolve.  The value is well-formed and its cadence is
    understood; what it asks for is a walk this step does not implement (a
    business-day shift, whose first author is plan step R8) or one that cannot
    terminate (a non-positive interval, which
    ``ck_recurrence_rules_positive_interval`` and the write door both refuse,
    so only a hand-built value can carry it).

    Raised rather than ignored because both alternatives are worse than an
    error: silently dropping the shift would date a bill on a day the user
    said it does not fall on, and a zero interval would spin forever emitting
    the same date.
    """


@dataclass(frozen=True)
class OccurrencePlacement:
    """One occurrence of a rule, and the pay period it lands in.

    **Two fields, because there is one fact to state.**  This carried a third
    until plan step C2-b2 -- a ``PlacementOutcome`` naming WHICH of two "no
    period" answers a ``None`` was, with a ``__post_init__`` refusing a value
    whose two fields disagreed.  The derived calendar tiles its covered span,
    so the SCHEDULE GAP half of that distinction stopped being constructible
    and the remaining member said only what :attr:`period` already said.  A
    fact stated twice needs a reconciler; a fact stated once does not, so the
    check went with the field rather than being kept passing.

    Attributes:
        occurrence: The date the cadence names.  For the ``PERIOD`` unit this
            is the paycheck's own payday; see the module docstring.
        period: The pay period the row lives in, or ``None`` when the SAVED
            schedule does not reach this occurrence.  ``None`` is a real
            answer, not an error, and it is ORDINARY -- see the module
            docstring for why it is no longer an operator signal.
    """

    occurrence: date
    period: DerivedPeriod | None


def _week_walk(anchor: date, interval_n: int) -> Iterator[date]:
    """Yield *anchor*, then every ``7 * interval_n`` days.

    Unbounded by design; see :func:`_month_ordinal_walk`.

    Args:
        anchor: The first occurrence.
        interval_n: Weeks between occurrences.  Must be positive.

    Yields:
        Occurrence dates, ascending, until the consumer stops pulling.  Walked
        past ``date.max`` it raises ``OverflowError`` rather than looping.
    """
    stride = timedelta(days=_DAYS_PER_WEEK * interval_n)
    day = anchor
    while True:
        yield day
        day += stride


def _period_walk(
    resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> Iterator[date]:
    """Yield the payday of every paycheck this rule fires in.

    The ``PERIOD`` unit's occurrences, and the reason
    :func:`occurrences` takes a calendar at all (finding D9).  A paycheck
    qualifies when it has not ENDED before the rule's bound -- the anchor --
    and when its index is in the rule's phase.  Both tests are verbatim what
    the reverse matcher applied
    (``p.end_date >= effective_from`` and
    ``(p.period_index - offset) % n == 0``), which is what made plan step
    R4a's cutover a no-op for every pay-period-space rule.

    **The phase is read from ``offset_periods`` rather than re-derived from
    the anchor**, and the difference is measurable.  Deriving it -- "the
    anchor's own period index, then every N-th after it" -- agrees whenever
    the anchor names a qualifying period, but ``_phased_period_anchor`` falls
    back to the raw bound when the schedule reaches NO period in phase (fewer
    than ``interval_n`` periods remain past the bound).  The derived form
    would then take the first remaining period, in phase or not, and generate
    a row the current engine does not.  Plan step R7c drops the
    ``offset_periods`` column, so the authored anchor has to carry the phase
    by construction from there; recorded here so that step does not
    rediscover it.

    Naturally bounded by the schedule, unlike its two siblings.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.

    Yields:
        Qualifying periods' ``start_date`` values, ascending.
    """
    for period in calendar.periods:
        if period.end_date < resolved.anchor_date:
            continue
        phase = period.period_index - resolved.offset_periods
        if phase % resolved.interval_n != 0:
            continue
        yield period.start_date


def _unbounded(
    resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> Iterator[date]:
    """Return the rule's raw occurrence sequence, before any bound.

    Total over :class:`~app.enums.RecurrenceUnitEnum`: every member has a
    walk, and an unrecognised value raises rather than returning an empty
    sequence that would read as "this rule never fires".

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.

    Returns:
        An ascending iterator of occurrence dates, unbounded except for the
        ``PERIOD`` unit, which the schedule bounds.

    Raises:
        RecurrenceGenerationError: When *resolved* names a unit this engine
            does not walk.
    """
    unit = resolved.unit
    if unit is RecurrenceUnitEnum.PERIOD:
        return _period_walk(resolved, calendar)
    if unit is RecurrenceUnitEnum.WEEK:
        return _week_walk(resolved.anchor_date, resolved.interval_n)
    # The day the rule MEANS, read through the ONE accessor that joins the
    # anchor's day to ``nominal_day`` (plan step R7a).  It was open-coded here
    # until the display describer needed the same join: two copies of "which of
    # these two fields holds the day" is how a rule comes to fire on the 31st
    # and read as the 30th.  It answers ``None`` for a unit that does not fire
    # on a day of the month, which after the two returns above means a unit
    # this engine has no walk for -- so ONE refusal covers both, and neither
    # ``walk_months`` nor ``clamped_day`` is ever handed a ``None`` to fail on
    # three frames down with a bare ``TypeError``.
    month_day = resolved.day_of_month
    if month_day is None:
        raise RecurrenceGenerationError(
            f"recurrence unit {unit!r} has no occurrence walk: it is neither "
            f"PERIOD nor WEEK and it names no day of the month.  Every member "
            f"of RecurrenceUnitEnum must have a walk -- returning nothing "
            f"would read as a rule that never fires -- and walking a "
            f"day-of-month cadence without a day would fire it on a "
            f"fabricated one."
        )
    # The SAME walk ``_resolution._calendar_anchor`` derives the anchor with
    # (``app.services.recurrence._months``), seeded at the anchor's own month
    # -- so the first date this yields IS the anchor, by construction rather
    # than by two implementations agreeing.  A YEAR cadence is that walk with
    # a twelve-month stride; there is no separate year arithmetic, so leap-day
    # clamping is the month clamp and cannot diverge from it.
    start = month_ordinal(resolved.anchor_date)
    if unit is RecurrenceUnitEnum.MONTH:
        return walk_months(start, month_day, resolved.interval_n)
    if unit is RecurrenceUnitEnum.YEAR:
        return walk_months(
            start, month_day, resolved.interval_n * MONTHS_PER_YEAR,
        )
    # A unit that DOES name a day of the month but has no stride here: the
    # sibling of the refusal above, reached by adding a member to
    # ``_resolution._DAY_OF_MONTH_UNITS`` without giving it a walk.  Two
    # refusals because the two half-finished edits are different, and each
    # names which one happened.
    raise RecurrenceGenerationError(
        f"recurrence unit {unit!r} names a day of the month but has no "
        f"occurrence walk.  Every member of RecurrenceUnitEnum must have "
        f"one: returning nothing instead would read as a rule that never "
        f"fires."
    )


def _bounded(
    raw: Iterator[date], resolved: ResolvedRecurrence, through: date,
) -> Iterator[date]:
    """Yield from *raw* until the first occurrence past any stopping bound.

    The ONE place a bound is applied, so the three walks cannot come to
    disagree about what ``end_date`` or ``max_occurrences`` means.  Every walk
    is ascending, so the first occurrence past a date bound is also the last
    one to check.

    ``end_date`` and ``max_occurrences`` are mutually exclusive
    (``ck_recurrence_rules_single_end_bound``); both are tested anyway, because
    a value built in memory is not the table and an untested second bound
    would be silently ignored rather than refused.

    Args:
        raw: The rule's unbounded occurrence sequence, ascending.
        resolved: The recurrence's two-axis meaning, carrying the bounds.
        through: The last day the caller asked about.

    Yields:
        The bounded occurrence dates, ascending.
    """
    emitted = 0
    for occurrence in raw:
        if occurrence > through:
            return
        if resolved.end_date is not None and occurrence > resolved.end_date:
            return
        if (
            resolved.max_occurrences is not None
            and emitted >= resolved.max_occurrences
        ):
            return
        emitted += 1
        yield occurrence


def _require_generable(resolved: ResolvedRecurrence) -> None:
    """Refuse a resolved value this engine cannot honour, before any walking.

    The three refusals every entry point makes FIRST, in one place so the
    composition cannot answer where :func:`occurrences` would raise -- which
    it did until a neutral review measured it: an empty schedule short-circuits
    to ``()`` before any occurrence is generated, so a business-day shift or a
    zero interval was silently accepted there and refused everywhere else.

    Args:
        resolved: The recurrence's two-axis meaning.

    Raises:
        RecurrenceGenerationError: When *resolved* asks for a business-day
            shift (plan step R8 is its first author), when its interval is not
            positive, or when its ``nominal_day`` disagrees with its anchor.
    """
    if resolved.shift is not BusinessDayShiftEnum.NONE:
        raise RecurrenceGenerationError(
            f"business-day shift {resolved.shift!r} is not implemented: plan "
            f"step R8 adds the weekend/holiday adjustment, and it needs a "
            f"holiday source this step does not have.  Generating the "
            f"unshifted dates instead would silently date every occurrence "
            f"on a day the rule says it does not fall on."
        )
    if resolved.interval_n < 1:
        raise RecurrenceGenerationError(
            f"recurrence interval_n must be positive, got "
            f"{resolved.interval_n}.  A non-positive interval emits the same "
            f"date forever, so this is refused rather than allowed to spin; "
            f"ck_recurrence_rules_positive_interval and "
            f"app.services.recurrence.resolve both refuse it upstream, so "
            f"only a hand-built value reaches here."
        )
    if resolved.nominal_day is None:
        return
    anchor = resolved.anchor_date
    last_day = calendar_module.monthrange(anchor.year, anchor.month)[1]
    if min(resolved.nominal_day, last_day) != anchor.day:
        raise RecurrenceGenerationError(
            f"nominal_day {resolved.nominal_day} clamps to "
            f"{min(resolved.nominal_day, last_day)} in {anchor:%B %Y}, not to "
            f"the anchor's own day {anchor.day}.  Presence of a nominal day "
            f"means the ANCHOR MONTH clamped it (ruling R-R3), so the pair "
            f"must agree; walking from a disagreeing pair would fire the "
            f"first occurrence on a day the anchor does not name.  "
            f"``resolve`` cannot produce this -- ``_month_anchor_day`` "
            f"records the day only when the anchor lands on its month's last "
            f"day -- but plan step R7c makes both independently-authored "
            f"columns whose only constraint is nominal_day BETWEEN 29 AND 31."
        )


def _placement_search(
    calendar: PayCalendar, placement: PeriodPlacementEnum,
) -> "Callable[[date], DerivedPeriod | None]":
    """Return the schedule search *placement* names, refusing an unknown one.

    Resolved ONCE per composition rather than per occurrence, which is also
    what makes an unrecognised placement an eager refusal instead of one that
    waits for an occurrence to exist.

    Args:
        calendar: The owner's pay-period schedule, which owns both searches.
        placement: Which placement rule the recurrence uses.

    Returns:
        The bound search method.

    Raises:
        RecurrenceGenerationError: When *placement* is not a member this
            engine has a rule for.
    """
    if placement is PeriodPlacementEnum.CONTAINING_DATE:
        return calendar.period_containing
    if placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER:
        return calendar.period_starting_on_or_after
    raise RecurrenceGenerationError(
        f"period placement {placement!r} has no rule.  Every member of "
        f"PeriodPlacementEnum must map an occurrence onto a period; "
        f"answering None instead would read as a schedule that cannot host "
        f"the row."
    )


def occurrences(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    *,
    through: date,
) -> Iterator[date]:
    """Return the dates this recurrence fires on, ascending, through *through*.

    The forward half of the redesign's generation model.  Walks the rule's own
    cadence from :attr:`ResolvedRecurrence.anchor_date`, applying the rule's
    closing bound and the caller's window.  See the module docstring for what
    an occurrence IS under each unit.

    **"Nothing before the anchor" holds for the calendar units only.**  Under
    the ``PERIOD`` unit the anchor is a BOUND (ruling R-R8) and the first
    occurrence is the payday of the paycheck that bound falls in, which is
    EARLIER than the anchor whenever the bound is mid-period -- deliberately,
    because that is where the cash leaves (plan step C9a).  So ruling R-R6's
    "occurrence-bounded" holds on the ``end_date`` side for every unit and on
    the opening side for the calendar units; ledger row D6 is the same
    asymmetry seen from the schema.

    **There is no LOWER window argument, and each CALLER that has one applies
    it.**  The production paths that state a lower bound narrow generation to a
    window the RULE does not state: both unarchive paths, both regenerate
    paths, ``period_population``'s batch boundary, the preview's display
    choice, and ``recurring_view``'s ``as_of``.  The paths that state NOTHING
    pass ``None`` since plan step R4b-1 -- the template create path,
    ``generate_transfers_for_all_periods`` and ``can_generate_in_period`` --
    because the two defaults ``resolve_generation_plan`` used to apply were
    already inside the anchor.  That is a per-call display / regeneration
    boundary rather than a property of the recurrence, and conflating the two
    is how defect D2 happened -- so it stays out of here.  Plan step R4a's
    adapter applied it for its callers; plan step R4b-2 deleted the adapter,
    and each caller now filters on the placed period's ``end_date``, which is
    the same predicate the reverse matcher applied to its candidate list.

    Refuses its impossible-to-honour inputs EAGERLY rather than on the first
    ``next()``: a caller that builds the iterator and passes it on would
    otherwise see the failure surface somewhere else entirely.

    Args:
        resolved: The recurrence's two-axis meaning, from
            :func:`app.services.recurrence.resolve`.
        calendar: The owner's pay-period schedule.  Required for the
            ``PERIOD`` unit, whose occurrences are paydays (finding D9), and
            unread by the other three.
        through: The last day to generate through.  Required, and there is
            deliberately no default: an unbounded walk over a calendar unit
            does not terminate.

    Returns:
        An ascending iterator of occurrence dates.  Empty when *through*
        precedes the anchor, when the rule's ``end_date`` does, or -- for the
        ``PERIOD`` unit -- when the schedule reaches no qualifying paycheck.

    Raises:
        RecurrenceGenerationError: See :func:`_require_generable`, plus a unit
            with no walk (:func:`_unbounded`).
    """
    _require_generable(resolved)
    return _bounded(_unbounded(resolved, calendar), resolved, through)


def place(
    occurrence: date,
    calendar: PayCalendar,
    placement: PeriodPlacementEnum,
) -> DerivedPeriod | None:
    """Return the pay period *occurrence* belongs in under *placement*.

    The placement half of the model: an occurrence is a calendar DATE and a
    Shekel row lives in a pay PERIOD, and this is the rule that carries one to
    the other.  Both branches bisect the schedule
    (:class:`~app.services.pay_calendar.PayCalendar`), which owns the search
    because "which period covers this day" is a question about the schedule.

    Args:
        occurrence: The date to place.
        calendar: The owner's pay-period schedule.
        placement: Which placement rule the recurrence uses.

    Returns:
        The :class:`~app.services.pay_calendar.DerivedPeriod` the row lives in,
        or ``None`` when the SAVED schedule holds no such period -- a date
        before it opens, or past its horizon.  Since plan step C2-b2 a date in
        a HOLE is not a third case: derived periods tile their covered span.

    Raises:
        RecurrenceGenerationError: When *placement* is a value this engine has
            no rule for.
    """
    return _placement_search(calendar, placement)(occurrence)


def occurrence_placements(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    *,
    through: date | None = None,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence in the window, paired with its pay period.

    The composition every reader of the table answers from, through
    :func:`~app.services.recurrence.rule_occurrences`: one forward walk, one
    placement per occurrence, and the pairs reported as generated.  Plan step
    R4a routed period selection here; plan step R4b-2 moved generation itself
    onto the pairs.  Materialised rather than lazy because every caller reads
    the result more than once.

    **Duplicated periods are reported, not collapsed.**  At a cadence longer
    than a month several occurrences legitimately land in one paycheck, and
    which row the user then owes is a generation decision -- see the module
    docstring.

    **An unplaced occurrence needs no reason field, since plan step C2-b2.**
    This paired every placement with a ``PlacementOutcome`` while ``None`` was
    two answers -- a schedule HOLE against "the schedule has not got there yet"
    -- and derived periods tile their covered span, so the first is
    unconstructible and the second is what ``period is None`` means.  The
    branch that told them apart, and the enum it wrote into, went with the
    state they described.

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.
        through: The last day to generate through.  ``None`` (the default)
            means the schedule's horizon, which is the last day a placement
            can succeed at all; pass a later date to see the occurrences
            beyond it, each carrying ``period=None``.

    Returns:
        One :class:`OccurrencePlacement` per occurrence, ascending by date.
        Empty for a schedule with no periods, where nothing can be placed and
        no window can be stated.

    Raises:
        RecurrenceGenerationError: See :func:`_require_generable` and
            :func:`_placement_search`.  Both run BEFORE the empty-schedule
            short-circuit, so this function refuses exactly what
            :func:`occurrences` and :func:`place` refuse rather than answering
            ``()`` over a value they would reject.
    """
    _require_generable(resolved)
    search = _placement_search(calendar, resolved.placement)
    horizon = calendar.horizon()
    if horizon is None:
        return ()
    window_end = horizon if through is None else through
    return tuple(
        OccurrencePlacement(occurrence=occurrence, period=search(occurrence))
        for occurrence in occurrences(resolved, calendar, through=window_end)
    )


__all__ = [
    "OccurrencePlacement",
    "RecurrenceGenerationError",
    "occurrence_placements",
    "occurrences",
    "place",
]
