"""
Shekel Budget App -- Resolving an authored recurrence into its two-axis view

One pure function, :func:`resolve`, turns what a caller AUTHORS
(:class:`RecurrenceSpec` -- a pattern and its parameters) into what the
recurrence MEANS (:class:`ResolvedRecurrence` -- an interval, a unit, a first
occurrence, a placement, a shift, the bounds, and the 0-or-1 nominal day).

**Nothing persists what this returns, and that is the design** (developer
ruling, 2026-08-07; plan step R2d).  The two-axis values are a DERIVATION over
the columns the row already holds -- the closed ``pattern_id`` set plus
``day_of_month`` / ``month_of_year`` / ``start_period_id`` / ``start_date`` /
``interval_n`` -- and the owner's pay-period schedule.  Storing a derivation
beside its own inputs is a cache; a cache drifts the moment one writer moves
one side alone, and no mechanism that polices it can be complete.  So it is
not stored: the two-axis view is computed where it is needed, from one
producer, and there is no second copy to disagree with the first.

The four values become COLUMNS -- authored, NOT NULL, from one backfill, in
the same transaction that drops the closed-set columns they were derived from
-- at plan step R7c, which is where the recurrence form starts collecting
them.  At that point they are authored rather than derived and storing them is
correct.  See ``c8f2b6a41d93``'s module docstring for the full reasoning.

**Every pattern this resolves NAMES A CADENCE, and a consumer may rely on
that.**  ``Once`` used to be the exception -- it meant "does not recur", so no
honest cadence existed for it, and it resolved to the same inert value as
``Every Period`` while four separate guards elsewhere did the real suppressing.
Plan step R2e-3 deleted it: "does not recur" is ``recurrence_rule_id IS NULL``
on either template kind, which never reaches this module at all.

Pure: no Flask, no ORM, no clock, no database.  Its two inputs are the
authored spec and the owner's :class:`~app.services.recurrence._calendar.PeriodCalendar`,
so every derivation below can be exercised at exact dates.

The four derivations
--------------------

1. **The effective start** -- the date the first occurrence is measured from.
   The GREATEST of the schedule's opening payday, the rule's ``start_date``,
   and its start period's ``start_date``.  That single maximum reproduces both
   of the engine's branches: ``match_periods`` applies the ``start_date``
   filter (``recurrence_engine.py:488``) AND an ``effective_from`` that
   ``resolve_generation_plan`` always supplies -- the start period's start when
   the rule has one, else the earliest pay period's (``:121-124``).

2. **A pay-period-space rule** (Every Period / Every N Periods) anchors on the effective
   start ITSELF, not on a period boundary.  ``anchor_date`` is the occurrence
   -- the date the rule targets -- and ``placement`` is what carries an
   occurrence onto a period; putting a period start in the anchor would put
   the result of the placement axis into the anchor axis, the exact fusion
   this redesign exists to undo.  It is also what makes the value TOTAL: plan
   step R2b anchored on "the first period ending on or after the bound", which
   does not exist when the bound falls past the materialised horizon --
   reachable today, because ``loan_recurrence_sync._sync_loan_cadence`` stamps
   ``start_date`` onto ANY rule, day-less every-paycheck ones included, so a
   loan originating past the horizon left no derivable anchor at all
   (developer ruling R-R8, 2026-08-05).  Under CONTAINING_DATE placement the
   two readings select the SAME period whenever the schedule covers the bound
   -- periods are ordered forward, so the first period ending on or after a
   date is the one containing it -- which is why all 11 live period-unit rules
   resolve identically either way.

   **``Every N Periods`` is the exception**, and a neutral review found it: its
   phase is ``(period_index - offset_periods) % interval_n == 0``, which a
   bare date cannot express, so anchoring it on the bound made the anchor and
   the rule's own stored ``offset_periods`` state DIFFERENT cadences
   (measured: stored phase 2 -- periods 2/5/8 -- against an anchor in period
   0 -- periods 0/3/6).  ``offset_periods`` is the one derived value that is
   still a COLUMN, so this is the one place the two can disagree, and it is
   why the anchor advances to the first period boundary that satisfies the
   phase.  Past the horizon it falls back to the bound, where no period exists
   to name and none would generate either.  See :func:`_phased_period_anchor`
   and :func:`_derive_offset_periods`.

3. **A calendar rule** (Monthly / Quarterly / Semi-Annual / Annual) anchors on
   the first date matching its ``(month_of_year, day_of_month)`` cycle on or
   after the effective start, month-end clamped exactly as ``_match_monthly``
   clamps (``min(day, monthrange(...))``, ``recurrence_engine.py:546``).
   ``or 1`` mirrors the engine's own coercion of a malformed rule
   (``:504-518``) rather than inventing a different one -- ``or``, not
   ``is not None``, so a 0 is coerced exactly as the engine coerces it.

4. **A Monthly First rule** anchors on the 1st of the first month whose OWN
   first paycheck falls on or after the effective start (developer ruling,
   2026-08-05).  "The 1st of the effective month" was ambiguous: for a rule
   starting mid-month it would place the first row in a paycheck EARLIER than
   the one the user chose, because the placement rule is "the first period
   starting on or after the occurrence".

   The schedule's own months are SCANNED, because "does this month qualify" is
   a question about that month's paydays and nothing else can answer it.  A
   one-step form -- "the month after the effective one always qualifies" --
   was tried and is wrong at any cadence longer than a month, where a month
   may have no payday at all; see :func:`_first_of_month_anchor` for the
   measured counterexample.

Bounds are NOT validated here.  ``end_date >= anchor_date`` is a real
invariant of the finished model and belongs to plan step R7c -- where the
anchor becomes a stored column -- together with the Marshmallow validator
that can refuse the pair at the door: 14 live rules resolve to an anchor in
the future, and refusing them here would make "stop this recurring bill"
raise.
"""
import calendar as calendar_module
from dataclasses import dataclass
from datetime import date
from itertools import islice

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.recurrence._calendar import PeriodCalendar, SchedulePeriod
from app.services.recurrence._months import (
    MONTHS_PER_YEAR,
    month_ordinal,
    walk_months,
)
from app.services.recurrence._vocabulary import modelled_pattern


class RecurrenceResolutionError(ShekelError):
    """A recurrence could not be resolved into a complete row.

    A broken invariant rather than bad user input, which is why it is not a
    ``ValidationError`` a route flashes: every user has had at least one pay
    period since registration bootstraps one (``auth_service.register_user``),
    and ``pattern_id`` is written only from
    :class:`~app.enums.RecurrencePatternEnum`.  Raised loudly so a recurrence
    can never be READ with a fabricated cadence, and -- because
    ``app.services.recurrence.author_rule`` resolves before it writes -- so a
    rule that cannot be resolved is never persisted in the first place.
    """


@dataclass(frozen=True)
class ResolvedRecurrence:  # pylint: disable=too-many-instance-attributes
    """What a recurrence MEANS, on the two axes, against one schedule.

    A computed value, never a row: see this module's docstring for why the
    derivation is not stored beside its own inputs.  It is what plan step R3's
    forward occurrence engine consumes, and what plan step R7c's migration
    freezes into columns once the form authors it directly.

    Pylint: ``too-many-instance-attributes`` (9/7) -- these nine ARE what one
    recurrence means, read as a flat unit by a single consumer, and the plan's
    END-state table (section 3) carries all but ``offset_periods``.  The two arguable
    sub-groups were both weighed and rejected: pairing ``end_date`` with
    ``max_occurrences`` behind a bound object would put their exclusivity in a
    second place beside the ``ck_recurrence_rules_single_end_bound`` CHECK
    that already owns it, and pairing ``anchor_date`` with ``nominal_day``
    would make every consumer unwrap a two-field object to ask for a date.
    Mirrors the :class:`RecurrenceSpec` and ``transfer_service.TransferSpec``
    precedents.

    Carries ENUM members rather than ``ref`` table ids because nothing
    persists it.  The ids exist to put a value in a column; a consumer asking
    "is this monthly" should compare
    ``resolved.unit is RecurrenceUnitEnum.MONTH``, not two integers whose
    meaning depends on a seed.  :func:`resolve` makes exactly one id-to-enum
    conversion -- the stored ``pattern_id`` -- and it goes through
    :func:`~app.services.recurrence.modelled_pattern`, which reads
    ``ref_cache``, the project's IDs-for-logic seam.  This module holds no
    other ``ref`` id at all.

    Attributes:
        offset_periods: The phase within the period cycle -- an
            ``Every N Periods`` rule fires where
            ``(period_index - offset_periods) % interval_n == 0``.  The
            LEGACY encoding of a fact ``anchor_date`` already carries, and the
            one derived value that is still a COLUMN on
            ``budget.recurrence_rules``; it is emitted here so the write door
            derives it in the SAME call that derives the anchor, rather than
            running the derivation twice and hoping the two agree.  Dies with
            the column at plan step R7c.
        interval_n: How many *unit*\\ s pass between occurrences.  Always the
            two-axis reading: 3 for Quarterly, 6 for Semi-Annual, the
            authored count for ``Every N Periods``, 1 elsewhere.
        unit: The cadence unit *interval_n* counts.
        anchor_date: The rule's phase, day and opening bound in one value.
            For the calendar units it IS the first occurrence, and occurrences
            are this date plus multiples of *interval_n* units.  **For the
            PERIOD unit it is the BOUND rather than the first occurrence**
            (ruling R-R8): a pay-period-space rule targets paychecks, and the
            first one it fires on is the paycheck that bound falls IN, whose
            payday is earlier than the bound whenever the bound is mid-period.
            That is deliberate -- it is where the cash leaves, and it is what
            lets a loan whose first installment falls mid-period bill in that
            period (plan step C9a).  Ledger row D6 is the same asymmetry seen
            from the schema.
        placement: How an occurrence DATE maps onto the pay period a row lives
            in.  The axis today's Monthly and Monthly First patterns differ
            on.
        shift: Weekend / holiday adjustment for the occurrence date.  Always
            ``NONE`` until plan step R8.
        end_date: The closing bound, or ``None`` for indefinite.  Mutually
            exclusive with *max_occurrences*
            (``ck_recurrence_rules_single_end_bound``).
        max_occurrences: The count-bounded end, or ``None``.  No writer sets
            it until plan step R8.
        nominal_day: The day the rule MEANS when *anchor_date*'s own month was
            too short to hold it -- April has no 31st, so a day-31 rule
            anchored there carries ``anchor_date = 2026-04-30`` and
            ``nominal_day = 31``.  ``None`` when the anchor holds the day
            itself, which is every rule whose day is 1-28 and every rule that
            does not fire on a day of the month at all.  Presence is the
            discriminator (ruling R-R3), and it is what stops a month-end rule
            from decaying to the 30th forever.
    """

    offset_periods: int
    interval_n: int
    unit: RecurrenceUnitEnum
    anchor_date: date
    placement: PeriodPlacementEnum
    shift: BusinessDayShiftEnum
    end_date: date | None
    max_occurrences: int | None
    nominal_day: int | None


#: Families of anchor derivation.  Three, because the anchor is measured in
#: three different spaces -- the paycheck rhythm, the calendar, and "each
#: month's first paycheck" -- not because there are three groups of patterns.
_FAMILY_PERIOD = "period"
_FAMILY_CALENDAR = "calendar"
_FAMILY_FIRST_OF_MONTH = "first_of_month"


@dataclass(frozen=True)
class _PatternDerivation:
    """The two-axis reading of one closed-set pattern.

    Attributes:
        interval_n: The interval the pattern names, or ``None`` for
            ``Every N Periods``, which keeps the authored one -- it is the one
            pattern whose interval was already a column rather than a name.
        unit: The cadence unit the pattern counts in.
        placement: How its occurrences map onto pay periods.
        family: Which anchor derivation applies (one of the ``_FAMILY_*``
            constants above).
        month_step: The interval expressed in MONTHS for the calendar family
            -- the residue class its occurrence months fall in -- and ``None``
            elsewhere.
    """

    interval_n: int | None
    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum
    family: str
    month_step: int | None


#: How each closed-set pattern reads on the two axes.
#:
#: Total over :class:`~app.enums.RecurrencePatternEnum`, and every entry names
#: a real cadence.  ``Once`` used to sit here holding INERT values copied from
#: ``Every Period`` -- byte-identical, so a consumer holding only a
#: :class:`ResolvedRecurrence` could not tell "does not recur" from "every
#: paycheck", and plan step R7c's downgrade could not have round-tripped
#: ``(1, period, containing_date)`` back to one pattern.  Plan step R2e-3
#: deleted the member instead (ruling R-R4 as amended by R-R11): "does not
#: recur" is ``recurrence_rule_id IS NULL``, which never reaches a resolver.
_PATTERN_DERIVATIONS: dict[RecurrencePatternEnum, _PatternDerivation] = {
    RecurrencePatternEnum.EVERY_PERIOD: _PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.PERIOD,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_PERIOD, month_step=None,
    ),
    RecurrencePatternEnum.EVERY_N_PERIODS: _PatternDerivation(
        interval_n=None, unit=RecurrenceUnitEnum.PERIOD,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_PERIOD, month_step=None,
    ),
    RecurrencePatternEnum.MONTHLY: _PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_CALENDAR, month_step=1,
    ),
    RecurrencePatternEnum.MONTHLY_FIRST: _PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        family=_FAMILY_FIRST_OF_MONTH, month_step=None,
    ),
    RecurrencePatternEnum.QUARTERLY: _PatternDerivation(
        interval_n=3, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_CALENDAR, month_step=3,
    ),
    RecurrencePatternEnum.SEMI_ANNUAL: _PatternDerivation(
        interval_n=6, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_CALENDAR, month_step=6,
    ),
    RecurrencePatternEnum.ANNUAL: _PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.YEAR,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        family=_FAMILY_CALENDAR, month_step=12,
    ),
}

#: The units whose anchor day can be month-end clamped, and therefore the only
#: ones that can need a ``recurrence_month_anchors`` row.
_CLAMPABLE_UNITS = (RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR)

#: The engine's coercion of a rule that names no day / month
#: (``recurrence_engine.py:504-518``), mirrored rather than re-invented.
_DEFAULT_DAY_OF_MONTH = 1
_DEFAULT_MONTH_OF_YEAR = 1

#: Upper bound on the month-ordinal walk in :func:`_calendar_anchor`.  Two
#: candidates always suffice (the effective month's own occurrence, then one
#: cycle later), so anything beyond this is a broken derivation, not a slow
#: one -- it raises instead of spinning.
_MAX_MONTH_PROBES = 4


@dataclass(frozen=True)
class RecurrenceSpec:  # pylint: disable=too-many-instance-attributes
    """What a caller AUTHORS about a recurrence.

    The closed-set vocabulary the form still speaks, which plan step R7
    replaces with the two-axis one; :func:`resolve` is the only thing that
    knows how one becomes the other.

    Pylint: ``too-many-instance-attributes`` (11/7) -- these are the
    irreducible inputs of one authoring request, exactly the fields the
    recurrence form collects, read as a flat unit by the single consumer
    (:func:`resolve`).  Mirrors the ``TransferSpec`` precedent.  Frozen so a
    constructed spec is an immutable record of one request.

    Attributes:
        user_id: The owning user.
        pattern_id: A ``ref.recurrence_patterns`` id.
        interval_n: Repeat every N pay periods.  Meaningful only for
            ``Every N Periods``; for every other pattern the interval is a
            property of the pattern and this is ignored, which is why an
            unconditional write of the form's hidden input reset a Quarterly
            rule's cadence to 1 (measured at R2b).
        offset_periods: Phase within the ``Every N Periods`` cycle.  Used only
            when no start period is given -- when one IS given, the phase is
            DERIVED from it, because that is the fact the user actually chose.
        day_of_month: Scheduling day for the calendar patterns.
        due_day_of_month: The real bill due day when it differs from the
            scheduling day.  Carried through untouched; plan step R5/R6 is
            where it becomes ``recurrence_due_dates``.
        month_of_year: Cycle-start month for quarterly / semi-annual / annual.
        start_period_id: The form's "First paycheck" choice.
        start_date: The rule's opening validity bound; written only by
            ``loan_recurrence_sync`` from the loan's first contractual
            installment (plan step C9a).
        end_date: The rule's closing validity bound.
        max_occurrences: The count-bounded end.  Mutually exclusive with
            ``end_date`` (``ck_recurrence_rules_single_end_bound``); no writer
            sets it until plan step R8.
    """

    user_id: int
    pattern_id: int
    interval_n: int = 1
    offset_periods: int = 0
    day_of_month: int | None = None
    due_day_of_month: int | None = None
    month_of_year: int | None = None
    start_period_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    max_occurrences: int | None = None


def _pattern_member(pattern_id: int) -> RecurrencePatternEnum:
    """Return the enum member *pattern_id* names, or RAISE.

    The read-side half of
    :func:`~app.services.recurrence.modelled_pattern`, which owns the lookup
    itself (including why it scans rather than inverting a map).  The two
    differ only in what an unmodelled id MEANS at each layer, and that
    difference is the whole reason both exist: at a form door an unmodelled id
    is user input to refuse with a flash, while here it names a rule already in
    the table whose cadence cannot be derived -- a broken invariant, raised
    loudly.  Built on the same function so the door and the reader can never
    disagree about which patterns the application models.

    Args:
        pattern_id: A ``ref.recurrence_patterns`` id.

    Returns:
        The matching :class:`~app.enums.RecurrencePatternEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *pattern_id*.
    """
    member = modelled_pattern(pattern_id)
    if member is not None:
        return member
    raise RecurrenceResolutionError(
        f"recurrence pattern id {pattern_id} matches no RecurrencePatternEnum "
        f"member.  A rule may only name a pattern this application models; "
        f"leaving one unresolved would persist a rule with no derivable "
        f"cadence."
    )


def _effective_start(
    spec: RecurrenceSpec,
    calendar: PeriodCalendar,
    start_period: SchedulePeriod | None,
) -> date:
    """Return the date this rule's first occurrence is measured from.

    See derivation 1 in the module docstring for why a single maximum
    reproduces both of the engine's branches.

    Args:
        spec: The authored recurrence.
        calendar: The owner's pay-period schedule.
        start_period: The spec's start period, already resolved, or ``None``.

    Returns:
        The composite opening bound.

    Raises:
        RecurrenceResolutionError: When the owner's schedule is empty, so
            there is no floor to measure against.
    """
    opening = calendar.opening_bound()
    if opening is None:
        raise RecurrenceResolutionError(
            f"user {spec.user_id} has no pay periods, so a recurrence has no "
            f"schedule to anchor against.  Registration bootstraps one "
            f"(auth_service.register_user), so an empty schedule here is a "
            f"broken invariant rather than a state to paper over."
        )
    bounds = [opening]
    bounds.extend(
        bound for bound in (
            spec.start_date,
            start_period.start_date if start_period is not None else None,
        )
        if bound is not None
    )
    return max(bounds)


def _calendar_anchor(
    effective: date, month_step: int, base_month: int, nominal_day: int,
) -> date:
    """Return the first ``(base_month, nominal_day)`` occurrence >= *effective*.

    Walks absolute month ordinals in the rule's residue class.  Because
    ``month_step`` divides 12 for every calendar pattern, a residue over month
    ordinals is the same set as the engine's residue over month NUMBERS -- so
    "every third month starting in April" names the identical months either
    way.  The day is clamped per month exactly as ``_match_monthly`` clamps it,
    which is what keeps a day-31 rule on the last day of every month.

    Args:
        effective: The rule's opening bound.
        month_step: The cycle length in months (1, 3, 6 or 12).
        base_month: The cycle's start month, 1-12.
        nominal_day: The day the rule means, 1-31, before clamping.

    Returns:
        The first occurrence date.

    Raises:
        RecurrenceResolutionError: When no candidate is found within
            :data:`_MAX_MONTH_PROBES` cycles, which is a derivation bug rather
            than a data one -- two candidates always suffice.
    """
    start_ordinal = month_ordinal(effective)
    target_residue = (base_month - 1) % month_step
    aligned = start_ordinal + (
        (target_residue - start_ordinal % month_step) % month_step
    )
    # The SAME walk plan step R3's engine generates occurrences with
    # (``app.services.recurrence._months``), seeded at this rule's residue
    # class -- so the anchor is provably that sequence's first element on or
    # after the bound, rather than a second implementation that agrees today.
    for candidate in islice(
        walk_months(aligned, nominal_day, month_step), _MAX_MONTH_PROBES,
    ):
        if candidate >= effective:
            return candidate
    raise RecurrenceResolutionError(
        f"no calendar anchor found within {_MAX_MONTH_PROBES} cycles of "
        f"{effective} for month_step={month_step} base_month={base_month} "
        f"nominal_day={nominal_day}.  Two candidates always suffice, so this "
        f"is a derivation bug, not a data one."
    )


def _next_month_first(day: date) -> date:
    """Return the 1st of the month after *day*'s.

    Args:
        day: Any date.

    Returns:
        The 1st of the following month, rolling the year at December.
    """
    if day.month == MONTHS_PER_YEAR:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _first_of_month_anchor(calendar: PeriodCalendar, effective: date) -> date:
    """Return the 1st of the first month whose own first paycheck qualifies.

    Derivation 4 in the module docstring, answered by SCANNING the schedule's
    own months, because "does this month qualify" is a question about that
    month's paydays and nothing else can answer it.  A month the schedule
    materialises no payday in cannot honour a rule that fires on each month's
    FIRST paycheck, so it is skipped -- which matters at a cadence longer than
    a month, where months genuinely have no payday at all
    (``cadence_days`` is user-selectable 1..365).

    **An earlier one-step form of this was wrong and the counterexample is
    worth keeping.**  It reasoned that the month AFTER the effective one
    always qualifies, since every payday in it falls on or after its own 1st.
    That holds only if the month HAS a payday.  On a 90-day cadence with
    paydays 2026-06-01 / 08-30 / 11-28, a bound of 2026-09-05 answered
    2026-10-01 -- a month with no paycheck -- where the ruling's answer is
    2026-11-01.  Three occurrences would then place into the single 2026-11-28
    period and collide on ``idx_transactions_template_period_scenario``.

    The scan cannot run past the materialised horizon, so a bound beyond it
    falls back to the one-step form: the schedule WILL extend, and at any
    cadence short enough for the pattern to mean anything the next month has a
    payday.  That keeps the derivation TOTAL, which plan step R7c's NOT NULL
    columns will require.  It also means the anchor of a ``Monthly First`` rule
    bounded past the horizon can move when the schedule extends -- inherent to
    a pattern defined in terms of paydays, not to this implementation, and
    equally true of the scan plan step R2b shipped.

    Args:
        calendar: The owner's pay-period schedule.
        effective: The rule's opening bound.

    Returns:
        The first occurrence date, always the 1st of a month.
    """
    for period in calendar.periods:
        if period.start_date < effective:
            continue
        earliest = calendar.earliest_start_in_month(
            period.start_date.year, period.start_date.month,
        )
        if earliest is not None and earliest >= effective:
            return date(period.start_date.year, period.start_date.month, 1)
    # Past the materialised horizon: no month can be inspected, so answer the
    # one the schedule will reach.  The effective month qualifies only if its
    # own first paycheck is on or after the bound.
    earliest = calendar.earliest_start_in_month(effective.year, effective.month)
    if earliest is not None and earliest >= effective:
        return date(effective.year, effective.month, 1)
    return _next_month_first(effective)


def _phased_period_anchor(
    calendar: PeriodCalendar, effective: date, interval_n: int, offset: int,
) -> date:
    """Return the first period start at or after *effective* in the phase.

    **The one place a period BOUNDARY belongs in the anchor**, and the reason
    is that ``Every N Periods`` fires on a subset of paychecks: its phase is
    ``(period_index - offset_periods) % interval_n == 0``
    (``recurrence_engine.py:500-502``), which the bound alone cannot express.
    Anchoring such a rule on the raw bound makes the two vocabularies state
    DIFFERENT cadences -- measured on the developer's schedule, an
    every-3-paychecks rule phased at 2 stored ``offset_periods = 2`` (the old
    engine fires periods 2, 5, 8) beside an anchor in period 0 (the two-axis
    reading fires 0, 3, 6), and plan step R4 would pick the second silently.

    Every other pay-period-space rule fires on EVERY paycheck, so its anchor
    is the bound itself and no boundary is stored (ruling R-R8).

    Args:
        calendar: The owner's pay-period schedule.
        effective: The rule's opening bound.
        interval_n: How many periods apart occurrences fall.
        offset: The phase within that cycle.

    Returns:
        The qualifying period's ``start_date``, or *effective* itself when the
        schedule reaches no qualifying period -- a bound past the materialised
        horizon, where there is no period to name and no row to generate
        either.  Keeping the value derivable is what plan step R7c's NOT
        NULL columns will require.
    """
    for period in calendar.periods:
        if period.end_date < effective:
            continue
        if (period.period_index - offset) % interval_n == 0:
            return period.start_date
    return effective


def _resolve_anchor(
    spec: RecurrenceSpec,
    derivation: _PatternDerivation,
    calendar: PeriodCalendar,
    effective: date,
    phase: tuple[int, int],
) -> tuple[date, int | None]:
    """Return this rule's first occurrence and the day it nominally means.

    Args:
        spec: The authored recurrence.
        derivation: The pattern's two-axis reading.
        calendar: The owner's pay-period schedule.
        effective: The rule's opening bound.
        phase: ``(interval_n, offset_periods)`` -- read only by the
            ``Every N Periods`` branch, where the anchor must carry the phase.

    Returns:
        ``(anchor_date, nominal_day)``.  ``nominal_day`` is ``None`` for
        every family whose occurrences are not day-of-month based, so no
        month-anchor row can belong to them.
    """
    if derivation.family == _FAMILY_PERIOD:
        interval_n, offset = phase
        if interval_n > 1:
            return _phased_period_anchor(
                calendar, effective, interval_n, offset,
            ), None
        return effective, None
    if derivation.family == _FAMILY_FIRST_OF_MONTH:
        return _first_of_month_anchor(calendar, effective), None
    # ``or``, NOT ``is not None``, and the difference is a live 500.  The
    # engine coerces with ``rule.day_of_month or 1`` / ``rule.month_of_year
    # or 1`` (``recurrence_engine.py:504-518``), so it maps 0 onto 1 as well
    # as NULL.  ``is not None`` let a 0 through to ``date(y, m, 0)``, which
    # raises -- and the preview endpoint reads both straight from
    # ``request.args``, where ``?day_of_month=0`` answered 200 before this
    # step.  A 0 month was worse than a crash: residue ``(0 - 1) % 12`` put
    # the anchor in DECEMBER where the engine puts it in January.  Mirroring
    # the coercion exactly is the point -- a second, different default is a
    # second answer to the same malformed rule.
    nominal_day = spec.day_of_month or _DEFAULT_DAY_OF_MONTH
    base_month = spec.month_of_year or _DEFAULT_MONTH_OF_YEAR
    return _calendar_anchor(
        effective, derivation.month_step, base_month, nominal_day,
    ), nominal_day


def _month_anchor_day(
    unit: RecurrenceUnitEnum, anchor: date, nominal_day: int | None,
) -> int | None:
    """Return the nominal day to record, or ``None`` when the anchor holds it.

    Presence is the discriminator (ruling R-R3): a
    ``budget.recurrence_month_anchors`` row exists exactly when
    ``anchor_date.day`` is no longer the day the user meant, which happens iff
    the anchor lands on its month's last day AND the nominal day is larger.  A
    rule whose day is 1-28 can never be clamped and costs nothing.

    Args:
        unit: The rule's cadence unit.
        anchor: The derived first occurrence.
        nominal_day: The day the rule means, or ``None`` for a family that
            does not fire on a day of the month.

    Returns:
        The nominal day when the anchor month clamped it, else ``None``.
    """
    if unit not in _CLAMPABLE_UNITS or nominal_day is None:
        return None
    last_day = calendar_module.monthrange(anchor.year, anchor.month)[1]
    if anchor.day == last_day and nominal_day > anchor.day:
        return nominal_day
    return None


def _require_owner(spec: RecurrenceSpec, calendar: PeriodCalendar) -> None:
    """Refuse a spec resolved against somebody else's schedule.

    An anchor is measured against a pay-period schedule, so pairing a rule
    with the wrong owner's calendar produces a first occurrence that is
    silently WRONG rather than an error -- and two call sites derive the
    calendar's owner from a different object than the rule's:
    ``loan_recurrence_sync.sync_recurring_payment_bounds`` uses
    ``account.user_id`` against a spec read from the rule, and
    ``pay_period_admin._repoint_recurrence_rules`` uses
    ``first_period.user_id``.  Both are consistent today; neither is
    enforced.  Checking the pairing here makes the assumption a fact.

    Args:
        spec: The authored recurrence.
        calendar: The schedule it is being resolved against.

    Raises:
        RecurrenceResolutionError: When the two name different users.
    """
    if calendar.user_id != spec.user_id:
        raise RecurrenceResolutionError(
            f"recurrence for user {spec.user_id} cannot be resolved against "
            f"user {calendar.user_id}'s pay-period schedule.  A first "
            f"occurrence is measured against the OWNER's schedule, so the "
            f"mismatched pair would produce a plausible wrong date rather "
            f"than an error."
        )


def _resolved_interval(
    spec: RecurrenceSpec, derivation: _PatternDerivation,
) -> int:
    """Return the two-axis interval, refusing a non-positive authored one.

    **The check is on the AUTHORED value, not the resolved one**, and the
    difference is a live defect an adversarial review measured.  For every
    calendar pattern the resolved interval is a hard-coded 1, 3, 6 or 1, which
    can never be non-positive -- so checking it looked at nothing, while
    ``app.services.recurrence._authoring._author`` wrote ``spec.interval_n``
    verbatim into a ``NOT NULL`` column carrying
    ``CHECK (interval_n > 0)``.  An authored 0 therefore reached the flush as
    an unhandled ``IntegrityError``.  The authored value is the one that
    becomes a column, so the authored value is the one the door must refuse;
    the resolved value is positive by construction once it is.

    Args:
        spec: The authored recurrence.
        derivation: The pattern's two-axis reading.

    Returns:
        The pattern's own interval, or the authored one for the single
        pattern (``Every N Periods``) whose interval was already a column.

    Raises:
        RecurrenceResolutionError: When the AUTHORED interval is not positive.
            Mirrors ``ck_recurrence_rules_positive_interval``, refused here so
            the caller sees the offending value rather than an IntegrityError
            at flush, and so the phase modulo cannot divide by zero.
    """
    if spec.interval_n < 1:
        raise RecurrenceResolutionError(
            f"recurrence interval_n must be positive, got {spec.interval_n} "
            f"for pattern id {spec.pattern_id} (user {spec.user_id}).  It is "
            f"written to a NOT NULL column with CHECK (interval_n > 0), so "
            f"letting it through would raise an unhandled IntegrityError at "
            f"the flush instead of here."
        )
    return (
        spec.interval_n if derivation.interval_n is None
        else derivation.interval_n
    )


def _derive_offset_periods(
    spec: RecurrenceSpec,
    pattern: RecurrencePatternEnum,
    interval_n: int,
    start_period: SchedulePeriod | None,
) -> int:
    """Return the ``offset_periods`` phase an authored recurrence fires on.

    Takes the already-resolved pattern, interval and start period rather than
    re-deriving them: :func:`resolve` is the only caller, it has all three in
    hand, and computing them twice for one authoring request is the redundant
    producer call the project rules out.

    DERIVED from the start period whenever the rule names one, because that is
    the fact the user actually chose: the form has no offset input at all (no
    template under ``app/templates/`` renders one), so a submitted value is
    always the schema default.  Applying the derivation on every write rather
    than only on create is what closes defect **D1** -- the update path wrote
    the default unconditionally, re-phasing every future occurrence of an
    ``Every N Periods`` rule on an amount-only edit.

    Args:
        spec: The authored recurrence.
        pattern: Its resolved pattern member.
        interval_n: Its resolved interval.
        start_period: Its start period, already looked up, or ``None``.

    Returns:
        The phase, always in ``0 .. interval_n - 1`` when derived.
    """
    if pattern is not RecurrencePatternEnum.EVERY_N_PERIODS:
        # Every other pattern ignores the column entirely; 0 is its default.
        return 0
    if start_period is None:
        return spec.offset_periods
    return start_period.period_index % interval_n


def resolve(spec: RecurrenceSpec, calendar: PeriodCalendar) -> ResolvedRecurrence:
    """Resolve an authored recurrence into its two-axis meaning.

    The single producer of that value.  Nothing persists what it returns --
    see this module's docstring -- so calling it twice for the same
    ``(spec, calendar)`` is the only way two readers can ever disagree, and it
    is a pure function, so they cannot.

    Args:
        spec: What the caller authored.
        calendar: The owner's pay-period schedule, which the anchor is
            measured against.

    Returns:
        The complete, internally-consistent :class:`ResolvedRecurrence`.

    Raises:
        RecurrenceResolutionError: When *spec* and *calendar* name different
            users, when ``pattern_id`` names no modelled pattern, when
            ``interval_n`` is not positive, or when the owner has no pay
            periods.  All four are broken invariants: a recurrence read with a
            fabricated cadence is worse than a refused read.
    """
    _require_owner(spec, calendar)
    pattern = _pattern_member(spec.pattern_id)
    derivation = _PATTERN_DERIVATIONS[pattern]
    interval_n = _resolved_interval(spec, derivation)

    start_period = calendar.period_by_id(spec.start_period_id)
    effective = _effective_start(spec, calendar, start_period)
    # The phase is resolved BEFORE the anchor, because an ``Every N Periods``
    # anchor has to carry it: a bare date cannot express
    # ``(period_index - offset) % interval_n == 0``, so anchoring such a rule
    # on the raw bound would state a different cadence from the one the row
    # holds (measured: stored phase 2 -- periods 2/5/8 -- against an anchor in
    # period 0 -- periods 0/3/6).
    offset_periods = _derive_offset_periods(
        spec, pattern, interval_n, start_period,
    )
    anchor, nominal_day = _resolve_anchor(
        spec, derivation, calendar, effective, (interval_n, offset_periods),
    )

    return ResolvedRecurrence(
        offset_periods=offset_periods,
        interval_n=interval_n,
        unit=derivation.unit,
        anchor_date=anchor,
        placement=derivation.placement,
        shift=BusinessDayShiftEnum.NONE,
        end_date=spec.end_date,
        max_occurrences=spec.max_occurrences,
        nominal_day=_month_anchor_day(derivation.unit, anchor, nominal_day),
    )
