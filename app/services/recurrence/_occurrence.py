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
period TWICE, which the paycheck-keyed generation index of the day could not
store), and a quarterly rule matched 1
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

* ``MONTH`` / ``YEAR`` -- the day ``starts_on`` names, in every
  *interval_n*-th month (or every *interval_n*-th YEAR, which is the same walk
  with a step of twelve), month-end clamped per month.  The day clamped from is
  :attr:`ResolvedRecurrence.nominal_day` when ``starts_on``'s own month was too
  short to hold it, and ``starts_on.day`` otherwise (ruling R-R3) -- which is
  what keeps a day-31 rule on the last day of every month instead of decaying
  to the 30th forever.  Both are read through
  :attr:`ResolvedRecurrence.day_of_month`, the one place that join is written.
* ``WEEK`` -- ``starts_on`` plus multiples of ``7 * interval_n`` days.  **This
  walk is correct and unreachable**, and plan step R8-a re-stated why: the unit
  is kept out of the offer set by
  :func:`~app.services.recurrence._frequency.has_row_date_coordinate`, because
  a weekly occurrence is neither a payday nor a day of the month and
  ``recurrence_engine.compute_due_date`` can date a generated row from nothing
  else.  Plan step **R5** gives a row its own ``occurs_on`` and the unit
  becomes authorable by that deletion.  It is implemented here rather than
  refused because a partial function over an enum is the defect this redesign
  exists to remove, and because a walk that silently ignored the unit would be
  a wrong answer rather than an error.
* ``PERIOD`` -- **the qualifying paycheck's own payday.**  A paycheck qualifies
  when it has not already ENDED before ``starts_on`` -- which is what the
  reverse matcher selected (``p.end_date >= effective_from``) -- and it is
  deliberate that a mid-period date bills in that period rather than the next:
  a loan whose first installment falls mid-period pays from that paycheck
  (plan step C9a).  **Since plan step R7c-b there is nothing to reconcile
  here**: ``resolve`` NORMALISES an authored date onto that paycheck's own
  payday before this walk ever sees it, so ``starts_on`` is this sequence's
  first element by construction.  Plan ledger row **D6** recorded the
  asymmetry that produced -- one field meaning an occurrence for three units
  and a BOUND for the fourth -- and it is what ruling R-R16 removed.

**A consequence worth stating rather than discovering: placement is INERT
under the ``PERIOD`` unit.**  Every occurrence it emits is a paycheck's own
``start_date``, and both placements carry such a date back to that same
paycheck -- ``CONTAINING_DATE`` because the period contains its own opening
day, and ``PERIOD_STARTING_ON_OR_AFTER`` because no earlier period starts
later.  Past the SAVED horizon both answer ``None`` instead, and they still
agree: since plan step R16-b-1 the walk names PROJECTED paydays too, and a
projected payday is not a row either search can return.  That is the ordinary
"the schedule has not got there yet", which is what ``period is None`` means
everywhere else here.  The
plan's section 3 says the opposite ("a mid-period bound places differently
under the two placements"); that claim reads the anchor as the emitted
occurrence, which the paragraph above is exactly the decision not to do.
Emitting the payday is what reproduced the reverse matcher's date as well as
its period: ``compute_due_date`` returns ``period.start_date`` for a rule with
no ``day_of_month``, so a mid-period bound emitted verbatim would have moved
the first row's date.

Bounds are OCCURRENCE bounds (ruling R-R6)
------------------------------------------

The closing bound (:class:`~app.services.recurrence.EndBound`) is applied to
the occurrence, not to the period it lands in.  The reverse matcher bounded
PERIODS -- the end date was tested against a period's START -- so it generated
rows dated outside the window the user stated: measured on the R1 baseline, a
monthly-15th rule ending 2025-06-05 generated a row due 2025-06-15.  That was
plan defect **D5**, and it died here: an occurrence the bound does not admit is
simply never emitted.

A COUNT bound counts OCCURRENCES the cadence names, including any the schedule
does not reach and never places.  "Stop after twelve" is a property of the
rule, not of how many rows the schedule happened to host.

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
(adversarial review, 2026-08-11, which measured all three shapes).

**All three are now UNCONSTRUCTIBLE, and the paragraph is kept as the record of
what the cutover crossed rather than as a live warning** (plan step
``pay_calendar:C4-c``).  Each was a STORED column disagreeing with the payday
set, and that step dropped both columns; there is one span and one ordinal,
computed on every read, so nothing is left to disagree.  *Until then this
paragraph said the states were unreachable because ``pay_period_write``
re-materialised the derivation on every write and "the owner's next payday
write REPAIRS them" -- true at the time and false since C4-c, which deleted
that machinery along with its subject.  The writer repairs nothing now because
there is nothing to repair.*  The three, as they were:

* **A HOLE was absorbed** (plan ledger row **P27**).  A stored ``end_date``
  short of the next payday left days uncovered; the derivation runs the
  preceding paycheck to the day before that payday, so an occurrence there
  seats against a real period id and generates a row where it used to be
  logged and skipped.
* **The stored CADENCE moved the horizon** (row **P28**).  The last period's
  derived end is ``payday + cadence_days - 1``, so a stored cadence that no
  longer matched the stored end moved the generation window -- SHORTER losing
  the occurrences past the new horizon, LONGER seating rows in a paycheck whose
  stored span ended before their date.  **This one has a survivor**: the
  cadence is still the sole input to the last period's PROJECTED end, so
  changing it still moves the horizon.  What is gone is the DISAGREEMENT --
  there is no stored end for it to come apart from.
* **A stored ORDINAL was re-derived** (row **P26**).  ``period_index`` is a
  period's position in payday order, so a stored ordinal that was not
  ``0..n-1`` re-phased every ``Every N Periods`` rule -- including one naming a
  start period, which the plan's first statement of P26 did not cover.

Two consequences ride on the first two.  Where the change puts a SECOND
occurrence of one template into one paycheck, both rows are now GENERATED and
stored -- plan step **R17** re-keyed the unique index onto the occurrence, and
the refusal a 30-day-or-longer cadence used to earn went with it.  Where it
does not repeat, the row is generated with a date
``compute_due_date`` reads off the paycheck's two ENDPOINT months rather than
off the occurrence this module found, so it can be dated in the wrong month
entirely -- plan ledger row **D18**, whose fix is recurrence plan step **R5**
(it gives the occurrence its own column and deletes ``compute_due_date``).
Both are measured and pinned by
``test_recurrence_engine.TestALegacyScheduleHole``.  Of the three shapes only
the HOLE ever had a detector -- ``scripts/integrity_check.py`` **BA-07**, a
query over the stored column -- and it died with that column at plan step
C4-c, by which point it had no subject either.

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

``budget.transactions`` HOLDS them since plan step **R17**:
``idx_transactions_template_scenario_occurrence`` is UNIQUE over
``(template, scenario, occurs_on)``.  The old index was keyed on the wrong
column -- it is a generation-idempotency guard, and a generated row's identity
is its OCCURRENCE, not its paycheck -- which is what made a repeat unstorable
and what made a MOVED row vacate its own occurrence (ledger row **D57**).

**The rows stay separate; only the grid sums them** (developer ruling,
2026-08-07).  Summing at generation would fit today's index, and that is
exactly why it is tempting -- it buys the fit by destroying which month a
payment covers, by freezing an amount that may change mid-group, and by
putting one row in front of several events.  So the pairs are returned as
generated: this module answers "what does the cadence name", and presentation
is the display layer's.

**A bill funded IN ADVANCE is already expressible, and the claim that it was
not was measured false at plan step R8-a.**  This paragraph read "a third
placement -- the LAST paycheck on or before the occurrence -- is what a bill
funded in advance needs, and the axis does not have it: today's two values both
fund on or after" (plan ledger row **D20**).  Both halves are wrong.
``CONTAINING_DATE`` funds from the paycheck whose span COVERS the occurrence,
so its payday is on or before that date by construction -- measured over five
pay cadences, 0 of 305 seated occurrences funded after theirs.  And the remedy
it named is not a third rule: ``PayCalendar.period_starting_on_or_before``
disagreed with ``period_containing`` on 0 of 8,460 days of a tiling calendar,
because derived periods TILE, and past the horizon it answers the LAST saved
paycheck -- which would seat every future occurrence of every rule in one.

What the axis genuinely cannot say is a LEAD: fund from a paycheck EARLIER than
the one containing the occurrence, so rent due 1 August is paid from the 17
July paycheck rather than the 31 July one.  D20 closed on the measurement and
that capability carries its own ledger row.

Pure: no Flask, no ORM, no clock, no database.
"""
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import DerivedPeriod, PayCalendar, paychecks_from
from app.services.recurrence._months import (
    month_ordinal,
    months_per_step,
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
    qualifies when it has not ENDED before ``starts_on`` and when its index is
    in the rule's phase.  Both tests are verbatim what the reverse matcher
    applied (``p.end_date >= effective_from`` and
    ``(p.period_index - offset) % n == 0``), which is what made plan step
    R4a's cutover a no-op for every pay-period-space rule.

    **The phase and the first occurrence are ONE fact, so the divergence this
    note used to record is gone** (plan step R7b-4).  It said: deriving the
    phase from the anchor agrees whenever the anchor names a qualifying period,
    but ``_phased_period_anchor`` fell back to the raw bound when the schedule
    reached NO period in phase, and the derived form would then take the first
    remaining period, in phase or not.  Both halves were symptoms of one cause:
    the phase was stored INDEPENDENTLY of the bound.  ``resolve`` reads the
    phase off ``starts_on`` now (``_derive_offset_periods``), the paycheck that
    date falls in is in phase by construction, and the advancing anchor is
    deleted.  ``resolved`` still carries ``offset_periods`` because this walk is
    the ONE reader of it; plan step R7c-c dropped the COLUMN it used to be
    written to, so what rides here is a derivation and nothing stores it.

    **``starts_on`` is this walk's own first yield, since plan step R7c-b**, and
    that is structural rather than checked: ``_resolution._first_occurrence``
    normalises an authored date onto the payday of the first paycheck not
    ending before it, which is this loop's admission test read directly.
    Nothing has to agree with anything.

    **It walks the owner's paychecks, saved AND projected, since plan step
    R16-b-1 -- and until then it TRUNCATED in silence.**  It iterated
    ``calendar.periods``, the SAVED set, so a caller asking for occurrences
    past the schedule's horizon got fewer dates than it asked for and no
    signal that it had: measured on a production clone (2026-08-27), an
    every-paycheck rule asked through ``2036-01-01`` answered 62 dates ending
    ``2028-07-27`` against the 255 that owner is actually paid in the window,
    the last ``2035-12-20`` -- 193 paydays dropped in silence.
    A truncated walk and a completed one are indistinguishable from the
    occurrences alone, which is the shape ledger row **P23** refuses one
    concept over -- an axis that covers part of its range reads exactly like
    one that covers all of it.

    The horizon is a MATERIALISATION boundary, not a fact about the cadence:
    an owner goes on being paid after the last payday anyone has saved, so
    this sequence goes on naming paydays.
    :func:`~app.services.pay_calendar.paychecks_from` is where
    that continuation lives, beside the derivation it continues, and it bounds
    the sequence at :data:`~app.utils.dates.CALENDAR_DATE_MAX` exactly as
    :func:`~._months.walk_months` does -- so this walk stops where the MONTH
    walk stops, and :func:`_bounded` is once again the ONLY place a bound is
    applied.  (:func:`_week_walk`, the third sibling, is bounded by ``date``
    itself rather than by that constant; the unit is unreachable until plan
    step R8-b opens it, and bounding it is that step's.)

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.

    Yields:
        Qualifying paychecks' ``start_date`` values, ascending -- saved where
        the schedule reaches, projected at the owner's cadence beyond it.
    """
    for period in paychecks_from(calendar, resolved.starts_on):
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
        An ascending iterator of occurrence dates, unbounded for every unit in
        the sense that nothing about the SCHEDULE stops one -- plan step
        R16-b-1 removed the ``PERIOD`` unit's exception, which was a SILENT
        truncation at the saved horizon rather than a bound anything had
        chosen.  Where each walk finally runs out still differs: ``PERIOD`` and
        the month-spanning units stop at
        :data:`~app.utils.dates.CALENDAR_DATE_MAX`, ``WEEK`` at ``date``'s own
        maximum.

    Raises:
        RecurrenceGenerationError: When *resolved* names a unit this engine
            does not walk.
    """
    unit = resolved.unit
    if unit is RecurrenceUnitEnum.PERIOD:
        return _period_walk(resolved, calendar)
    if unit is RecurrenceUnitEnum.WEEK:
        return _week_walk(resolved.starts_on, resolved.interval_n)
    # The day the rule MEANS, read through the ONE accessor that joins
    # ``starts_on``'s day to ``nominal_day`` (plan step R7a).  It was open-coded here
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
    # Seeded at ``starts_on``'s own month, so the first date this yields IS the
    # authored first occurrence -- by construction rather than by two
    # implementations agreeing.  **Until plan step R7c-b there WAS a second
    # implementation**: ``_resolution._calendar_anchor`` walked these same month
    # ordinals to derive the anchor on every read, and the two agreed because
    # they shared ``_months``.  The date is authored now, so the derivation is
    # deleted and there is nothing left to share it with.  **The STRIDE** was a
    # second copy of its own: this function computed ``interval_n *
    # MONTHS_PER_YEAR`` for a YEAR cadence while the anchor took one off the
    # pattern table, so ``months_per_step`` states it once.  A YEAR cadence is
    # that walk with a twelve-month stride; there is no separate year
    # arithmetic, so leap-day clamping is the month clamp and cannot diverge
    # from it.
    #
    # ``months_per_step`` is partial over the enum and is NOT guarded here,
    # because reaching this line already proves membership: ``day_of_month``
    # answers non-``None`` only for ``_frequency.has_day_of_month_coordinate``,
    # which is a membership test against ``_months.MONTH_SPANNING_UNITS``
    # itself, which is that function's own key set.  (It read
    # ``_resolution._DAY_OF_MONTH_UNITS`` until plan step R8-a, an alias of the
    # same tuple that moved to ``_frequency`` with the predicate.)  A guard
    # whose only reachability condition is "two hand-written sets
    # disagree" is the fence this project removes rather than tests, and an
    # adversarial review of plan step R7b-1 named it: the sets are one now, so
    # the state is unconstructible rather than merely unreached.
    start = month_ordinal(resolved.starts_on)
    return walk_months(
        start, month_day, months_per_step(unit, resolved.interval_n),
    )


def _bounded(
    raw: Iterator[date], resolved: ResolvedRecurrence, through: date,
) -> Iterator[date]:
    """Yield from *raw* until the first occurrence past any stopping bound.

    The ONE place a bound is applied, so the three walks cannot come to
    disagree about what a closing bound means.  Every walk is ascending, so the
    first occurrence the bound refuses is also the last one to check.

    **Two branches until plan step R7b-3, and now none.**  It tested
    ``end_date`` and ``max_occurrences`` separately and tested BOTH even though
    ``ck_recurrence_rules_single_end_bound`` refuses the pair, "because a value
    built in memory is not the table".  The bound is one value with three
    shapes now (:class:`~app.services.recurrence.EndBound`), so there is no
    pair to be defensive about and no shape this loop can fail to handle: a
    bound plan step R8 adds arrives with its own
    :meth:`~app.services.recurrence.EndBound.admits`, and this function does
    not change for it.

    Args:
        raw: The rule's unbounded occurrence sequence, ascending.
        resolved: The recurrence's two-axis meaning, carrying the bound.
        through: The last day the caller asked about.

    Yields:
        The bounded occurrence dates, ascending.
    """
    emitted = 0
    for occurrence in raw:
        if occurrence > through:
            return
        if not resolved.end_bound.admits(
            emitted=emitted, occurrence=occurrence,
        ):
            return
        emitted += 1
        yield occurrence


def _require_generable(resolved: ResolvedRecurrence) -> None:
    """Refuse a resolved value this engine cannot honour, before any walking.

    The refusals every entry point makes FIRST, in one place so the
    composition cannot answer where :func:`occurrences` would raise -- which
    it did until a neutral review measured it: an empty schedule short-circuits
    to ``()`` before any occurrence is generated, so a business-day shift or a
    zero interval was silently accepted there and refused everywhere else.

    **A THIRD refusal left at plan step R7c-b, and it left by becoming
    UNCONSTRUCTIBLE rather than by being dropped.**  It checked that
    ``nominal_day`` clamped to the day ``starts_on`` carries -- a pair that says
    one thing twice, which the schema could not fully express and so a guard
    stood in for.  That step completes ``ck_recurrence_rules_nominal_day`` with
    the clamp equality and moves the in-memory half into
    :meth:`~._resolution.ResolvedRecurrence.__post_init__`, so the value cannot
    exist and there is no state for a walk-time fence to catch.  A guard whose
    only reachability condition is "somebody built the pair by hand" is the
    fence this project removes rather than tests.

    Args:
        resolved: The recurrence's two-axis meaning.

    Raises:
        RecurrenceGenerationError: When *resolved* asks for a business-day
            shift (plan step R8 is its first author), or when its interval is
            not positive.
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
    cadence from :attr:`ResolvedRecurrence.starts_on`, applying the rule's
    closing bound and the caller's window.  See the module docstring for what
    an occurrence IS under each unit.

    **"Nothing before ``starts_on``" holds for EVERY unit, since plan step
    R7c-b.**  It held for the calendar units only while the ``PERIOD`` unit
    anchored on a BOUND (ruling R-R8) whose paycheck's payday could be earlier
    -- one field with two meanings, plan ledger row **D6**.  ``resolve``
    normalises that date onto the paycheck's own payday now, so ruling R-R6's
    "occurrence-bounded" holds on both sides for all four units and the
    asymmetry is gone rather than documented.

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
        precedes the anchor, when the rule's closing bound admits none of
        them, or -- for the ``PERIOD`` unit -- when the owner has no payday at
        all.  **It is COMPLETE through *through* since plan step R16-b-1**, up to
        :data:`~app.utils.dates.CALENDAR_DATE_MAX`, past which this application
        names no date: the ``PERIOD`` walk used to stop at the SAVED schedule's
        horizon and say nothing, so a caller asking past it was answered short
        (62 dates against 255, measured on a production clone 2026-08-27).  The
        schedule having "not got there yet" is a fact about PLACEMENT
        (:func:`place` answering ``None``), never about whether the cadence
        fires.

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
