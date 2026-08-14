"""
Shekel Budget App -- Resolving an authored recurrence into its two-axis view

One pure function, :func:`resolve`, turns what a caller AUTHORS
(:class:`RecurrenceSpec` -- a cadence and its parameters) into what the
recurrence MEANS (:class:`ResolvedRecurrence` -- an interval, a unit, a first
occurrence, a placement, a shift, the bounds, and the 0-or-1 nominal day).

**Since plan step R7b the CADENCE is authored rather than translated.**  A
caller states ``(interval_n, unit, placement)``; this module never sees a
``ref.recurrence_patterns`` id and holds no ``ref`` id at all.  The closed set
survives only as the STORAGE encoding, crossed by two functions in
:mod:`app.services.recurrence._frequency`: ``encode_cadence`` at the write door
and ``decode_pattern`` at the read door.  Plan step R7c deletes both and this
module does not change.

**Plan step R7b-2 removed ``decode_pattern``'s callers outside the package.**
The two form doors and the preview each translated a posted ``pattern_id``
until the form started authoring the axes directly; nothing above the package
posts or decodes a pattern id now.  ``cadence_of`` still has two outside
callers that read the COLUMN -- ``obligations_aggregator`` and
``calendar_infrequency`` -- so "the decode is in one place" remains false in
that one direction; what IS true is that they reach the same function, so
there is one mapping rather than several.  Plan step R7c retires both with the
columns.

**What is still DERIVED, and therefore still not stored** (developer ruling,
2026-08-07; plan step R2d): the first occurrence, and the phase the ``PERIOD``
unit fires on.  Both are functions of the authored spec AND the owner's
pay-period schedule, so storing either beside its inputs would be a cache; a
cache drifts the moment one writer moves one side alone, and no mechanism that
polices it can be complete.

``anchor_date`` becomes a COLUMN -- authored, NOT NULL, from one backfill, in
the same transaction that drops the closed-set columns -- at plan step R7c.  At
that point it is authored rather than derived and storing it is correct.  See
``c8f2b6a41d93``'s module docstring for the full reasoning.

**Every cadence this resolves NAMES A REAL RHYTHM, and a consumer may rely on
that.**  ``Once`` used to be the exception -- it meant "does not recur", so no
honest cadence existed for it, and it resolved to the same inert value as
"every paycheck" while four separate guards elsewhere did the real suppressing.
Plan step R2e-3 deleted it: "does not recur" is ``recurrence_rule_id IS NULL``
on either template kind, which never reaches this module at all.

Pure: no Flask, no ORM, no clock, no database.  Its two inputs are the
authored spec and the owner's
:class:`~app.services.pay_calendar.PayCalendar`, so every derivation below can
be exercised at exact dates.

The four derivations
--------------------

1. **The effective start** -- the date the first occurrence is measured from.
   The GREATEST of the schedule's opening payday, the rule's ``start_date``,
   and its start period's ``start_date``.  That single maximum reproduces both
   of the reverse matcher's branches: it applied the ``start_date`` filter
   itself AND an ``effective_from`` that ``resolve_generation_plan`` used to
   default -- the start period's start when the rule has one, else the earliest
   pay period's.

   **Plan step R4b-1 DELETED both of those defaults**, precisely because this
   maximum already subsumes them: no walk emits an occurrence placed before the
   anchor, so a lower window bound equal to one of these three values can never
   drop a row the anchor has not already dropped.  ``effective_from`` is now a
   caller's display / regeneration boundary and nothing else, and ``None``
   means it stated none.  The equivalence was measured, not argued: identical
   answers for all 46 live production rules over all 61 periods, and a
   byte-identical ``tests/oracles/recurrence_baseline.txt`` over the 428
   shapes it then held (430 since plan step R4b-2 added D10's).

2. **A pay-period-space rule** (the ``PERIOD`` unit) anchors on the effective
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

   **An interval above 1 is the exception**, and a neutral review found it: the
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

3. **A calendar rule** (the ``MONTH`` and ``YEAR`` units under
   ``CONTAINING_DATE``) anchors on
   the first date matching its ``(month_of_year, day_of_month)`` cycle on or
   after the effective start, month-end clamped as
   :func:`app.services.recurrence._months.clamped_day` clamps
   (``min(day, monthrange(...))``, which is what the reverse matcher plan step
   R4a replaced did per period).  ``or 1`` mirrors that matcher's coercion of
   a rule naming no day rather than inventing a different one -- ``or``, not
   ``is not None``, so a 0 is coerced the same way.  A day or month outside
   its own column's domain is REFUSED rather than coerced or clamped; see
   :func:`_require_authored_calendar_fields`.

4. **A month-scale rule funded from the month's FIRST paycheck**
   (``PERIOD_STARTING_ON_OR_AFTER``) anchors on the 1st of the first month
   whose OWN first paycheck falls on or after the effective start (developer
   ruling,
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

``end_date >= anchor_date`` is NOT validated here.  It is a real invariant of
the finished model and belongs to plan step R7c -- where the anchor becomes a
stored column -- together with the Marshmallow validator that can refuse the
pair at the door: 14 live rules resolve to an anchor in the future, and
refusing them here would make "stop this recurring bill" raise.

**The bound's own shape needs no validation at all, since plan step R7b-3.**
"At most one closing bound" and "a count names at least one occurrence" are
carried by :class:`~app.services.recurrence.EndBound`, which cannot express
either violation, so this module holds no refusal for them and neither does
anything else.  What IS refused here is the set of column DOMAINS the write
door writes verbatim -- see :func:`_require_authored_domains`.
"""
import calendar as calendar_module
from dataclasses import dataclass
from datetime import date
from itertools import islice

from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._bounds import NEVER_ENDS, EndBound
from app.services.recurrence._frequency import (
    FAMILY_CALENDAR,
    FAMILY_FIRST_OF_MONTH,
    FAMILY_PERIOD,
    RecurrenceResolutionError,
    anchor_family,
    require_positive_interval,
)
from app.services.recurrence._months import (
    MONTH_SPANNING_UNITS,
    MONTHS_PER_YEAR,
    month_ordinal,
    months_per_step,
    walk_months,
)


@dataclass(frozen=True)
class ResolvedRecurrence:  # pylint: disable=too-many-instance-attributes
    """What a recurrence MEANS, on the two axes, against one schedule.

    A computed value, never a row: see this module's docstring for why the
    derivation is not stored beside its own inputs.  It is what plan step R3's
    forward occurrence engine consumes, and what plan step R7c's migration
    freezes into columns once the form authors it directly.

    Pylint: ``too-many-instance-attributes`` (8/7) -- these eight ARE what one
    recurrence means, read as a flat unit by a single consumer, and the plan's
    END-state table (section 3) carries all but ``offset_periods``.  Pairing
    ``anchor_date`` with ``nominal_day`` was weighed and rejected: it would
    make every consumer unwrap a two-field object to ask for a date.  Mirrors
    the :class:`RecurrenceSpec` and ``transfer_service.TransferSpec``
    precedents.

    **``end_date`` and ``max_occurrences`` DID pair, at plan step R7b-3**, and
    the note here used to argue they should not -- that a bound object "would
    put their exclusivity in a second place beside the
    ``ck_recurrence_rules_single_end_bound`` CHECK that already owns it".  That
    is true of a wrapper holding two optional fields and false of a value with
    three shapes: :class:`~app.services.recurrence.EndBound` cannot state two
    bounds at all.  What that removes is not the CHECK -- the table still needs
    it, for writers that never see this type -- nor
    ``end_bound_from_columns``'s refusal, which PARSES untyped storage.  It
    removes the exclusivity from the WRITERS, which is where it could actually
    be got wrong: ``loan_recurrence_sync`` states its change as
    ``replace(spec, end_bound=payoff)``, and with two independent fields the
    same call would leave a count sitting beside the date it just wrote.

    Carries ENUM members rather than ``ref`` table ids because nothing
    persists it.  The ids exist to put a value in a column; a consumer asking
    "is this monthly" should compare
    ``resolved.unit is RecurrenceUnitEnum.MONTH``, not two integers whose
    meaning depends on a seed.  Since plan step R7b the spec carries members
    too, so this module makes NO id-to-enum conversion and holds no ``ref`` id
    at all -- the one conversion left in the package is
    ``_frequency.decode_pattern``'s, at the read door.

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
        end_bound: When the recurrence STOPS -- indefinitely, on a date, or
            after a count of occurrences.  ONE value with three shapes, so
            "at most one closing bound"
            (``ck_recurrence_rules_single_end_bound``) is a state the type
            cannot express rather than one anything has to check; see
            :mod:`app.services.recurrence._bounds`.
        nominal_day: The day the rule MEANS when *anchor_date*'s own month was
            too short to hold it -- April has no 31st, so a day-31 rule
            anchored there carries ``anchor_date = 2026-04-30`` and
            ``nominal_day = 31``.  ``None`` when the anchor holds the day
            itself, which is every rule whose day is 1-28 and every rule that
            does not fire on a day of the month at all.  Presence is the
            discriminator (ruling R-R3), and it is what stops a month-end rule
            from decaying to the 30th forever.  **Read it through
            :attr:`day_of_month`**, never directly: the day a rule MEANS is the
            two fields taken together, and open-coding that join is how a
            second answer starts.
    """

    offset_periods: int
    interval_n: int
    unit: RecurrenceUnitEnum
    anchor_date: date
    placement: PeriodPlacementEnum
    shift: BusinessDayShiftEnum
    end_bound: EndBound
    nominal_day: int | None

    @property
    def day_of_month(self) -> int | None:
        """Return the day of the month this recurrence fires on.

        The ONE reader of the ``(anchor_date, nominal_day)`` pair, which is one
        fact stored in two fields: the anchor holds the day unless its own
        month was too short to hold it, in which case :attr:`nominal_day` holds
        what the rule meant and the anchor holds the clamp (ruling R-R3).  The
        occurrence walk and the display describer both need that day, and
        writing the join twice is how the same rule comes to fire on the 31st
        and read as the 30th.

        ``is None``, not truthiness: :attr:`nominal_day`'s domain is 29-31, but
        a falsy-day bug here would silently re-clamp every later month.

        Returns:
            The day 1-31 the rule means, month-end clamped per month by the
            walk itself -- or ``None`` for a unit that does not fire on a day
            of the month (:data:`_DAY_OF_MONTH_UNITS`).  ``None`` is absence
            rather than a missing value: a paycheck-space or weekly rule has no
            day-of-month to name, and answering the anchor's own day would
            invent a coordinate the cadence never uses.
        """
        if self.unit not in _DAY_OF_MONTH_UNITS:
            return None
        if self.nominal_day is None:
            return self.anchor_date.day
        return self.nominal_day


#: The units that fire on a DAY OF THE MONTH, and therefore the only ones whose
#: anchor day can be month-end clamped.
#:
#: Named for what it decides rather than for the clamp: it answers both
#: :attr:`ResolvedRecurrence.day_of_month` ("does this cadence have such a day
#: at all") and :func:`_month_anchor_day` ("can that day have been clamped").
#: The clamp is a consequence of firing on a day of the month, not a separate
#: property, so one constant is honest for both readers.
#:
#: **DERIVED from the month-span table rather than written out**, which an
#: adversarial review of plan step R7b-1 required: a literal ``(MONTH, YEAR)``
#: here is a second statement of :data:`~._months.MONTH_SPANNING_UNITS`, and the
#: only way to reach ``months_per_step``'s refusal from this module was for the
#: two to disagree.  Deriving makes that unreachable by construction instead of
#: by a guard -- see :func:`_resolve_anchor`, which no longer carries one.
_DAY_OF_MONTH_UNITS = MONTH_SPANNING_UNITS

#: The reverse matcher's coercion of a rule that names no day / month
#: (``rule.day_of_month or 1`` / ``rule.month_of_year or 1``), mirrored rather
#: than re-invented.
_DEFAULT_DAY_OF_MONTH = 1
_DEFAULT_MONTH_OF_YEAR = 1

#: The domains ``ck_recurrence_rules_dom``, ``ck_recurrence_rules_due_dom``,
#: ``ck_recurrence_rules_moy`` and ``ck_recurrence_rules_valid_offset`` bound
#: their columns to.  Named once, so the door and the table state one domain
#: rather than two that happen to agree.  The two day columns share one pair
#: because they hold the same KIND of value -- a day of a month -- and giving
#: them separate constants would invite them to drift apart.
_DAY_OF_MONTH_MIN = 1
_DAY_OF_MONTH_MAX = 31
_MONTH_OF_YEAR_MIN = 1
_MONTH_OF_YEAR_MAX = 12
_MIN_OFFSET_PERIODS = 0

#: Upper bound on the month-ordinal walk in :func:`_calendar_anchor`.  Two
#: candidates always suffice (the effective month's own occurrence, then one
#: cycle later), so anything beyond this is a broken derivation, not a slow
#: one -- it raises instead of spinning.
_MAX_MONTH_PROBES = 4


@dataclass(frozen=True)
class RecurrenceSpec:  # pylint: disable=too-many-instance-attributes
    """What a caller AUTHORS about a recurrence.

    **The TWO-AXIS vocabulary since plan step R7b.**  It carried a
    ``pattern_id`` from the closed ``ref.recurrence_patterns`` set until then,
    which made the authored vocabulary and the derived one two different
    languages with :func:`resolve` translating between them on every read.  A
    caller now states the cadence it means -- an interval, a unit and a
    placement -- and exactly two functions in
    :mod:`app.services.recurrence._frequency` cross the line to the columns:
    ``encode_cadence`` on the way in and ``decode_pattern`` on the way out.
    Plan step R7c deletes both, and nothing above the door moves.

    Pylint: ``too-many-instance-attributes`` (11/7) -- these are the
    irreducible inputs of one authoring request, exactly the fields the
    recurrence form collects, read as a flat unit by the single consumer
    (:func:`resolve`).  Mirrors the ``TransferSpec`` precedent.  Frozen so a
    constructed spec is an immutable record of one request.

    Attributes:
        user_id: The owning user.
        unit: The cadence unit -- what *interval_n* counts.
        interval_n: How many *unit*\\ s pass between occurrences.  Meaningful
            for EVERY unit since plan step R7b, which is the point: it was
            read only for ``Every N Periods`` while the other three cadences
            baked their interval into a pattern NAME, and that fusion is this
            arc's root cause.
        placement: Which pay period funds an occurrence.  **INERT under the
            ``PERIOD`` unit** -- a pay-period-space rule emits a paycheck's own
            ``start_date`` and both placements carry such a date back to that
            same period (proven in :mod:`._occurrence`) -- so it defaults to
            ``CONTAINING_DATE``, the reading under which the occurrence is
            funded by the paycheck it falls in.
        offset_periods: Phase within the ``PERIOD`` cycle.  Used only when no
            start period is given -- when one IS given, the phase is DERIVED
            from it, because that is the fact the user actually chose.  **A
            LEGACY column carried through rather than authored**: no form
            renders an input for it, and plan step R7c drops it once the
            authored anchor carries the phase by construction (plan ledger rows
            D21, D24).
        day_of_month: Scheduling day for a cadence measured in months or
            years.  Plan step R7c renames the column to ``nominal_day``.
        due_day_of_month: The real bill due day when it differs from the
            scheduling day.  Carried through untouched; plan step R5 is where
            it becomes ``transactions.due_on``.
        month_of_year: Cycle-start month for a cadence that skips months.
            Plan step R7c renames the column to ``nominal_month``.
        start_period_id: The form's "First paycheck" choice.  Retired by plan
            step R7b's own last leaf, which replaces it with a DATE.
        start_date: The rule's opening validity bound; written only by
            ``loan_recurrence_sync`` from the loan's first contractual
            installment (plan step C9a).
        end_bound: The rule's closing validity bound -- indefinite, a date, or
            a count of occurrences, as ONE value
            (:class:`~app.services.recurrence.EndBound`).  Replacing it is how
            a closing bound CHANGES, which is what makes
            ``replace(spec, end_bound=...)`` safe for the one writer that owns
            a bound it did not author: ``loan_recurrence_sync`` states the
            loan's payoff and the shape it replaces cannot leave a count
            behind.  Defaults to :data:`~app.services.recurrence.NEVER_ENDS`,
            which 41 of the 46 live rules carry.
    """

    user_id: int
    unit: RecurrenceUnitEnum
    interval_n: int = 1
    placement: PeriodPlacementEnum = PeriodPlacementEnum.CONTAINING_DATE
    offset_periods: int = 0
    day_of_month: int | None = None
    due_day_of_month: int | None = None
    month_of_year: int | None = None
    start_period_id: int | None = None
    start_date: date | None = None
    end_bound: EndBound = NEVER_ENDS


def _effective_start(
    spec: RecurrenceSpec,
    calendar: PayCalendar,
    start_period: DerivedPeriod | None,
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
    way.  The day is clamped per month by
    :func:`app.services.recurrence._months.clamped_day`, which is what keeps a
    day-31 rule on the last day of every month.

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


def _first_of_month_anchor(calendar: PayCalendar, effective: date) -> date:
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
    calendar: PayCalendar, effective: date, interval_n: int, offset: int,
) -> date:
    """Return the first period start at or after *effective* in the phase.

    **The one place a period BOUNDARY belongs in the anchor**, and the reason
    is that ``Every N Periods`` fires on a subset of paychecks: its phase is
    ``(period_index - offset_periods) % interval_n == 0``
    (``_occurrence._period_walk``), which the bound alone cannot express.
    Anchoring such a rule on the raw bound makes the two vocabularies state
    DIFFERENT cadences -- measured on the developer's schedule, an
    every-3-paychecks rule phased at 2 stored ``offset_periods = 2`` (the old
    engine fires periods 2, 5, 8) beside an anchor in period 0 (the two-axis
    reading fires 0, 3, 6), and plan step R4a would have picked the second
    silently.

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
    family: str,
    calendar: PayCalendar,
    effective: date,
    offset_periods: int,
) -> tuple[date, int | None]:
    """Return this rule's first occurrence and the day it nominally means.

    Args:
        spec: The authored recurrence.
        family: Its anchor derivation, from :func:`anchor_family`.
        calendar: The owner's pay-period schedule.
        effective: The rule's opening bound.
        offset_periods: The resolved phase -- read only by the ``PERIOD``
            family, where the anchor must carry it.

    Returns:
        ``(anchor_date, nominal_day)``.  ``nominal_day`` is ``None`` for
        every family whose occurrences are not day-of-month based, so no
        month-anchor row can belong to them.

    Raises:
        RecurrenceResolutionError: When *family* is one this function has no
            derivation for.  **Total over the ``FAMILY_*`` constants rather
            than falling through to the calendar branch**, which an adversarial
            review of plan step R7b-1 required: the calendar branch was the
            implicit ``else``, so a family added to :func:`anchor_family` and
            forgotten here -- plan step R8 adds one for the WEEK unit -- would
            have taken it and anchored a weekly rule on a month cycle.
    """
    if family == FAMILY_PERIOD:
        if spec.interval_n > 1:
            return _phased_period_anchor(
                calendar, effective, spec.interval_n, offset_periods,
            ), None
        return effective, None
    if family == FAMILY_FIRST_OF_MONTH:
        return _first_of_month_anchor(calendar, effective), None
    if family != FAMILY_CALENDAR:
        raise RecurrenceResolutionError(
            f"anchor family {family!r} has no derivation.  Every family "
            f"anchor_family can return must have one here: taking the "
            f"calendar branch by default would anchor a cadence on a month "
            f"cycle it does not run on."
        )
    # ``is None``, not ``or``, and the change is plan step R4a's.  The reverse
    # matcher coerced with ``rule.day_of_month or 1``, mapping 0 onto 1
    # alongside NULL -- which was the only thing standing between
    # ``?day_of_month=0`` on the unvalidated preview endpoint and
    # ``date(y, m, 0)``.  ``_require_authored_calendar_fields`` now REFUSES a
    # stated 0 at the door, so the only value reaching here is NULL, whose
    # meaning is "this rule names no day" and whose default is the matcher's
    # own.  Truthiness would still read a 0 as absent, which is the shape this
    # project's coding rules rule out: absence is ``is None``.
    nominal_day = (
        _DEFAULT_DAY_OF_MONTH if spec.day_of_month is None
        else spec.day_of_month
    )
    base_month = (
        _DEFAULT_MONTH_OF_YEAR if spec.month_of_year is None
        else spec.month_of_year
    )
    # ``months_per_step`` is partial over the enum and it is NOT guarded here,
    # because reaching this line already proves membership: the calendar family
    # requires ``spec.unit in _DAY_OF_MONTH_UNITS``, which IS
    # ``_months.MONTH_SPANNING_UNITS``, which is the key set of the table
    # ``months_per_step`` reads.  A guard would be a fence over an impossible
    # state; deriving the one set from the other is what makes it impossible.
    return _calendar_anchor(
        effective,
        months_per_step(spec.unit, spec.interval_n),
        base_month,
        nominal_day,
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
    if unit not in _DAY_OF_MONTH_UNITS or nominal_day is None:
        return None
    last_day = calendar_module.monthrange(anchor.year, anchor.month)[1]
    if anchor.day == last_day and nominal_day > anchor.day:
        return nominal_day
    return None


def _require_owner(spec: RecurrenceSpec, calendar: PayCalendar) -> None:
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


def _require_authored_domains(spec: RecurrenceSpec) -> None:
    """Refuse an authored value outside the domain its own column allows.

    **Plan step R4a moved this refusal here, and it was previously an
    accident.**  ``recurrence_engine._match_annual`` called
    ``calendar.monthrange(year, month_of_year)`` directly, so a rule naming
    month 13 raised ``ValueError`` -- the only thing in the application that
    refused it, and reachable live: ``templates.preview_recurrence`` reads
    ``month_of_year`` straight from ``request.args``, so
    ``?month_of_year=13`` on an Annual pattern was a 500.  The forward engine
    walks month ORDINALS instead (``_months.walk_months``), where ``13`` is
    simply ``(13 - 1) % 12 == 0`` -- January -- and a day of ``99`` clamps to
    the month's last day.  Deleting the old matcher without this would trade a
    loud crash for a plausible wrong date, which is the worse of the two.

    The check is on the AUTHORED value for the same reason
    :func:`~app.services.recurrence._frequency.require_positive_interval`'s is:
    ``app.services.recurrence._authoring._author`` writes ``spec.day_of_month``
    / ``spec.due_day_of_month`` / ``spec.month_of_year`` verbatim into columns
    carrying ``ck_recurrence_rules_dom``, ``ck_recurrence_rules_due_dom`` and
    ``ck_recurrence_rules_moy``, so an out-of-domain value reaches the flush as
    an unhandled ``IntegrityError`` naming neither the field nor the value.
    Refusing at the door names both.

    **``NULL`` is the only value that means "this rule states no day", and
    ``0`` is REFUSED.**  A neutral review measured the first draft of this
    function checking the COERCED value (``spec.day_of_month or 1``), which let
    a ``0`` past the door and straight into the flush as the very
    ``IntegrityError`` the paragraph above says it exists to prevent.  The
    column is nullable, and the reverse matcher's ``or 1`` conflated ``NULL``
    with ``0`` only because Python truthiness does; the CHECK does not, and
    neither does this.  A stated ``None`` still resolves to
    :data:`_DEFAULT_DAY_OF_MONTH` in :func:`_resolve_anchor`, which is the
    READER's job -- so the coercion and the domain live in one place each
    instead of one place doing both and disagreeing at zero.

    **Applied to every pattern, not only the calendar ones**, because it is
    the COLUMN's domain rather than the walk's: ``_author`` writes the value
    whatever the pattern, so an ``Every Period`` rule carrying
    ``day_of_month = 32`` is refused here even though nothing would ever read
    the field.  The reverse matcher ignored it for such a rule; that was the
    field being unread, not the value being legal.

    **Plan step R7b-3 closed plan ledger row D23 here and in
    :mod:`app.services.recurrence._bounds`.**  Four CHECK constraints on
    ``budget.recurrence_rules`` reached the flush unmirrored, and they did not
    all have the same remedy.  ``single_end_bound`` and
    ``positive_max_occurrences`` are properties of a SHAPE -- "at most one
    closing bound", "a count names at least one occurrence" -- so they went
    into the type and no refusal is written for them anywhere: no value can
    break them.  ``due_dom`` and ``valid_offset`` are DOMAINS over plain
    integers, which is the same thing ``dom`` and ``moy`` are, so they are
    mirrored here beside them.  Making those two structural as well means a
    day-of-month value type, which is plan step **G2**'s work.

    ``offset_periods`` is checked on the AUTHORED value even though the write
    door writes the RESOLVED one, and the two cannot differ in the direction
    that matters: :func:`_derive_offset_periods` answers ``0``, the authored
    value, or ``start_period.period_index % interval_n`` -- and a period index
    is a schedule ordinal, so only the middle arm can carry a negative.

    Args:
        spec: The authored recurrence.

    Raises:
        RecurrenceResolutionError: When a STATED day or due day is outside
            1-31, a stated month is outside 1-12, or the phase is negative.
            ``None`` states nothing and passes.
    """
    for field, day in (
        ("day_of_month", spec.day_of_month),
        ("due_day_of_month", spec.due_day_of_month),
    ):
        if day is None or _DAY_OF_MONTH_MIN <= day <= _DAY_OF_MONTH_MAX:
            continue
        raise RecurrenceResolutionError(
            f"recurrence {field} must be NULL or between "
            f"{_DAY_OF_MONTH_MIN} and {_DAY_OF_MONTH_MAX}, got {day} for a "
            f"{spec.unit!r} recurrence (user {spec.user_id}).  It is "
            f"written to a column carrying ck_recurrence_rules_dom or "
            f"ck_recurrence_rules_due_dom, so letting it through would raise "
            f"an unhandled IntegrityError at the flush; and an over-large day "
            f"would be CLAMPED to a month's last day, answering a plausible "
            f"date the rule never named."
        )
    if spec.offset_periods < _MIN_OFFSET_PERIODS:
        raise RecurrenceResolutionError(
            f"recurrence offset_periods must be at least "
            f"{_MIN_OFFSET_PERIODS}, got {spec.offset_periods} for a "
            f"{spec.unit!r} recurrence (user {spec.user_id}).  It is written "
            f"to a NOT NULL column carrying ck_recurrence_rules_valid_offset, "
            f"and a negative phase also makes "
            f"``(period_index - offset) % interval_n`` select a different set "
            f"of paychecks than the one the rule names."
        )
    month = spec.month_of_year
    if (
        month is not None
        and not _MONTH_OF_YEAR_MIN <= month <= _MONTH_OF_YEAR_MAX
    ):
        raise RecurrenceResolutionError(
            f"recurrence month_of_year must be NULL or between "
            f"{_MONTH_OF_YEAR_MIN} and {_MONTH_OF_YEAR_MAX}, got {month} for "
            f"a {spec.unit!r} recurrence (user {spec.user_id}).  It is "
            f"written to a column carrying ck_recurrence_rules_moy, and the "
            f"month-ordinal walk would otherwise read it MODULO 12 -- month "
            f"13 silently becoming January."
        )


def _derive_offset_periods(
    spec: RecurrenceSpec, start_period: DerivedPeriod | None,
) -> int:
    """Return the ``offset_periods`` phase an authored recurrence fires on.

    Takes the already-looked-up start period rather than re-deriving it:
    :func:`resolve` is the only caller, it has it in hand, and looking it up
    twice for one authoring request is the redundant producer call the project
    rules out.

    DERIVED from the start period whenever the rule names one, because that is
    the fact the user actually chose: the form has no offset input at all (no
    template under ``app/templates/`` renders one), so a submitted value is
    always the schema default.  Applying the derivation on every write rather
    than only on create is what closes defect **D1** -- the update path wrote
    the default unconditionally, re-phasing every future occurrence of an
    every-N-paychecks rule on an amount-only edit.

    **A cadence of every ONE unit has phase 0 by construction**, whatever the
    row holds: every paycheck qualifies, so ``index % 1`` is 0 for all of them.
    Stating that ahead of the derivation is what reproduces the closed-set rule
    this replaced exactly -- ``Every Period`` returned 0 unconditionally while
    ``Every N Periods`` with ``N = 1`` derived a 0, and the two-axis reading
    cannot tell those apart because they are the same cadence.

    Args:
        spec: The authored recurrence.
        start_period: Its start period, already looked up, or ``None``.

    Returns:
        The phase, always in ``0 .. interval_n - 1`` when derived.
    """
    if spec.unit is not RecurrenceUnitEnum.PERIOD or spec.interval_n == 1:
        # No other cadence reads the column at all; 0 is its default.
        return 0
    if start_period is None:
        return spec.offset_periods
    return start_period.period_index % spec.interval_n


def resolve(spec: RecurrenceSpec, calendar: PayCalendar) -> ResolvedRecurrence:
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
            users, when ``interval_n`` is not positive, when the
            ``(unit, placement)`` pair has no anchor derivation, when
            ``day_of_month`` / ``due_day_of_month`` / ``month_of_year`` /
            ``offset_periods`` is outside its column's domain, or when the
            owner has no pay periods.  All five are broken invariants: a
            recurrence read with a fabricated cadence is worse than a refused
            read.
    """
    _require_owner(spec, calendar)
    require_positive_interval(
        spec.interval_n,
        f"a {spec.unit!r} recurrence (user {spec.user_id})",
    )
    family = anchor_family(spec.unit, spec.placement)
    _require_authored_domains(spec)

    start_period = calendar.period_by_id(spec.start_period_id)
    effective = _effective_start(spec, calendar, start_period)
    # The phase is resolved BEFORE the anchor, because an every-N-paychecks
    # anchor has to carry it: a bare date cannot express
    # ``(period_index - offset) % interval_n == 0``, so anchoring such a rule
    # on the raw bound would state a different cadence from the one the row
    # holds (measured: stored phase 2 -- periods 2/5/8 -- against an anchor in
    # period 0 -- periods 0/3/6).
    offset_periods = _derive_offset_periods(spec, start_period)
    anchor, nominal_day = _resolve_anchor(
        spec, family, calendar, effective, offset_periods,
    )

    return ResolvedRecurrence(
        offset_periods=offset_periods,
        interval_n=spec.interval_n,
        unit=spec.unit,
        anchor_date=anchor,
        placement=spec.placement,
        shift=BusinessDayShiftEnum.NONE,
        end_bound=spec.end_bound,
        nominal_day=_month_anchor_day(spec.unit, anchor, nominal_day),
    )
