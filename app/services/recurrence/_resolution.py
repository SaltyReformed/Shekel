"""
Shekel Budget App -- Resolving an authored recurrence into every column

One pure function, :func:`resolve`, turns what a caller AUTHORS
(:class:`RecurrenceSpec` -- a pattern and its parameters) into what the row
HOLDS (:class:`ResolvedRecurrence` -- every column of
``budget.recurrence_rules``, both vocabularies, plus the 0-or-1
``recurrence_month_anchors`` value).

**Why one function emits both vocabularies.**  Plan step R2b added the
two-axis columns beside the closed ``pattern_id`` set they replace, and until
step R4 the OLD ones are what the engine reads.  Two vocabularies describing
one cadence is a persisted copy of a derivation -- the B-14 defect shape -- and
the way a copy drifts is that some writer moves one side and not the other.
Emitting both from a single call over a single input removes the opportunity:
there is no intermediate state in which one has been written and the other has
not, because the caller never holds the halves separately.

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

2. **A pay-period-space rule** (Every Period / Once) anchors on the effective
   start ITSELF, not on a period boundary.  ``anchor_date`` is the occurrence
   -- the date the rule targets -- and ``placement_id`` is what carries an
   occurrence onto a period; storing a period start in the anchor would put
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
   bare date cannot express, so anchoring it on the bound made the two
   vocabularies state DIFFERENT cadences (measured: stored phase 2 against an
   anchor in period 0, i.e. periods 2/5/8 against 0/3/6).  Its anchor
   therefore advances to the first period boundary that satisfies the phase --
   and falls back to the bound past the horizon, where no period exists to
   name and none would generate either.  See :func:`_phased_period_anchor`.

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
invariant of the finished model and belongs to plan step R7, together with the
Marshmallow validator that can refuse the pair at the door: 14 live rules
carry a derived anchor in the future, and refusing them here would make
"stop this recurring bill" raise.
"""
import calendar as calendar_module
from dataclasses import dataclass
from datetime import date

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.models.recurrence_rule import ResolvedRecurrence
from app.services.recurrence._calendar import PeriodCalendar, SchedulePeriod


class RecurrenceResolutionError(ShekelError):
    """A recurrence could not be resolved into a complete row.

    A broken invariant rather than bad user input, which is why it is not a
    ``ValidationError`` a route flashes: every user has had at least one pay
    period since registration bootstraps one (``auth_service.register_user``),
    and ``pattern_id`` is written only from
    :class:`~app.enums.RecurrencePatternEnum`.  Raised loudly so a rule can
    never be persisted with a fabricated cadence -- which is what plan step
    R2c's NOT NULL tightening then makes structural.
    """


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


#: How each closed-set pattern reads on the two axes.  ``Once`` does not
#: recur, so no honest cadence exists for it; it takes INERT values (ruling
#: R-R4) and ``pattern_id = Once`` REMAINS what suppresses generation until
#: plan step R9 deletes the rows.
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
    RecurrencePatternEnum.ONCE: _PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.PERIOD,
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

#: Months in a year, for the absolute month-ordinal arithmetic.
_MONTHS_PER_YEAR = 12


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
    """Return the enum member *pattern_id* names.

    Resolved by comparing INTEGER ids through ``ref_cache``, never by reading
    a ``name`` column -- the project-wide IDs-for-logic invariant.  Scanning
    the eight cached members rather than adding an inverse map to
    ``ref_cache`` is deliberate: rule authoring happens on a template edit,
    not per row of a grid, so the inverse the account-category classifier
    needed (ruling R-CV) would buy nothing here.

    Args:
        pattern_id: A ``ref.recurrence_patterns`` id.

    Returns:
        The matching :class:`~app.enums.RecurrencePatternEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *pattern_id*.
    """
    for member in RecurrencePatternEnum:
        if ref_cache.recurrence_pattern_id(member) == pattern_id:
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
    start_ordinal = effective.year * _MONTHS_PER_YEAR + (effective.month - 1)
    target_residue = (base_month - 1) % month_step
    ordinal = start_ordinal + (
        (target_residue - start_ordinal % month_step) % month_step
    )
    for _probe in range(_MAX_MONTH_PROBES):
        year, month_index = divmod(ordinal, _MONTHS_PER_YEAR)
        month = month_index + 1
        day = min(nominal_day, calendar_module.monthrange(year, month)[1])
        candidate = date(year, month, day)
        if candidate >= effective:
            return candidate
        ordinal += month_step
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
    if day.month == _MONTHS_PER_YEAR:
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
    payday.  That keeps the derivation TOTAL, which plan step R2c-3's NOT NULL
    tightening requires.  It also means the anchor of a ``Monthly First`` rule
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
        either.  Keeping the value derivable is what plan step R2c-3's NOT
        NULL tightening requires.
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


def _resolve_phase(
    spec: RecurrenceSpec,
    pattern: RecurrencePatternEnum,
    interval_n: int,
    start_period: SchedulePeriod | None,
) -> int:
    """Return the ``offset_periods`` phase this rule fires on.

    DERIVED from the start period whenever the rule names one, because that
    is the fact the user actually chose: the form has no offset input at all
    (no template under ``app/templates/`` renders one), so a submitted value
    is always the schema default.  Applying the derivation on every write
    rather than only on create is what closes defect **D1** -- the update path
    wrote the default unconditionally, re-phasing every future occurrence of
    an ``Every N Periods`` rule on an amount-only edit.

    Args:
        spec: The authored recurrence.
        pattern: The resolved pattern member.
        interval_n: The rule's resolved interval.
        start_period: The spec's start period, already resolved, or ``None``.

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
    """Resolve an authored recurrence into every column of its row.

    Args:
        spec: What the caller authored.
        calendar: The owner's pay-period schedule, which the anchor is
            measured against.

    Returns:
        The complete, internally-consistent :class:`ResolvedRecurrence`.

    Raises:
        RecurrenceResolutionError: When ``pattern_id`` names no modelled
            pattern, when ``interval_n`` is not positive, or when the owner
            has no pay periods.  All three are broken invariants: a rule with
            a fabricated cadence is worse than a refused write.
    """
    pattern = _pattern_member(spec.pattern_id)
    derivation = _PATTERN_DERIVATIONS[pattern]
    interval_n = (
        spec.interval_n if derivation.interval_n is None
        else derivation.interval_n
    )
    if interval_n < 1:
        # Mirrors ``ck_recurrence_rules_positive_interval``, refused here so
        # the phase modulo below cannot divide by zero and so the caller sees
        # the offending value rather than an IntegrityError at flush.
        raise RecurrenceResolutionError(
            f"recurrence interval_n must be positive, got {interval_n} for "
            f"pattern id {spec.pattern_id} (user {spec.user_id})."
        )

    start_period = calendar.period_by_id(spec.start_period_id)
    effective = _effective_start(spec, calendar, start_period)
    # The phase is resolved BEFORE the anchor, because an ``Every N Periods``
    # anchor has to carry it: the two vocabularies would otherwise state
    # different cadences for the same rule.
    offset_periods = _resolve_phase(spec, pattern, interval_n, start_period)
    anchor, nominal_day = _resolve_anchor(
        spec, derivation, calendar, effective, (interval_n, offset_periods),
    )

    return ResolvedRecurrence(
        user_id=spec.user_id,
        pattern_id=spec.pattern_id,
        interval_n=interval_n,
        offset_periods=offset_periods,
        day_of_month=spec.day_of_month,
        due_day_of_month=spec.due_day_of_month,
        month_of_year=spec.month_of_year,
        start_period_id=spec.start_period_id,
        start_date=spec.start_date,
        end_date=spec.end_date,
        unit_id=ref_cache.recurrence_unit_id(derivation.unit),
        anchor_date=anchor,
        placement_id=ref_cache.period_placement_id(derivation.placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        max_occurrences=spec.max_occurrences,
        nominal_day=_month_anchor_day(derivation.unit, anchor, nominal_day),
    )
