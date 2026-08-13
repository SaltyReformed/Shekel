"""What a recurrence's cadence IS, with no schedule to measure it against.

Plan step **R7a-2b**.  :func:`app.services.recurrence.resolve` answers what a
recurrence MEANS against one owner's pay calendar, and most of that answer does
not need the calendar at all: a pattern's interval, its unit and its placement
are properties of the PATTERN, and only the anchor -- where the first
occurrence lands -- needs a schedule.  This module is that schedule-free half,
split out so a consumer holding no calendar can still ask how often something
repeats.

**The split is what makes the monthly equivalent possible.**
``obligations_aggregator`` turns a per-occurrence amount into a monthly one for
every recurring template on ``/savings`` and the Recurring surface, and it has
no :class:`~app.services.pay_calendar.PayCalendar`, and neither do its
savings-dashboard callers.  (The Recurring surface DOES hold one and reads
``calendar.cadence`` off it -- an adversarial review corrected an earlier
"nor do their callers" here, which was checkable and wrong.)
Before this step it therefore could not use the two-axis vocabulary at all and
read ``pattern_id`` through a seven-branch switch, which is why a cadence plan
step R8 authors -- ``(2, MONTH)`` -- would have read "Every 2 months" in the
Recurrence cell beside a BLANK monthly figure.  One expression over
``(interval_n, unit)`` answers for every cadence, including the ones nothing
authors yet.

**ONE table, read twice.**  :data:`PATTERN_DERIVATIONS` moved here from
``_resolution`` rather than being copied: that module reads this table for the
pattern half of its own answer, so the schedule-free reading and the anchored
one cannot disagree about what a pattern means.  A second table would be the
defect this arc exists to remove, one file over.  The dependency runs ONE way
-- ``_resolution`` imports this module and this module imports nothing of it --
which is what keeps the split a boundary rather than a pair of files that need
each other.

**Since plan step R7b this module is also the SEAM between the two
vocabularies.**  A caller AUTHORS ``(interval_n, unit, placement)`` and the
table stores a ``ref.recurrence_patterns`` id, so exactly two functions cross
that line: :func:`encode_cadence` (authored -> stored, used by the write door)
and :func:`decode_pattern` (stored -> authored, used by the read door).  Both
read :data:`PATTERN_DERIVATIONS` -- the encoder through an INVERSION of it
computed at import, never a second hand-written table -- so the round trip is
one statement of the mapping read in two directions and cannot half-drift.

**Since plan step R7b-2 it also holds the ANCHOR FAMILY router**
(:func:`anchor_family` and the ``FAMILY_*`` constants), moved here from
``_resolution`` on a developer ruling 2026-08-13.  It is the same split one
level down: WHICH derivation a ``(unit, placement)`` cadence uses is a fact
about the cadence and needs no schedule, while WHERE that derivation puts the
anchor needs the whole calendar and stays in ``_resolution``.  The move also
gave the recurrence FORM a public name to ask the question with --
:func:`fires_on_day_of_month`, the projection that decides whether the picker
renders a Day of Month input -- rather than a second hand-written list of which
cadences have a day-of-month coordinate, which is precisely the shape of
duplication this arc removes.

Everything keyed on the closed pattern set dies at plan step **R7c**, which
makes ``interval_n`` and ``unit_id`` authored columns: the encoder and decoder
above, the table and its inverse leave together, and nothing above the door
changes.  What survives is :class:`Cadence` -- the pair itself --
:meth:`Cadence.occurrences_per_year`, and the family router, all three being
facts about the two axes rather than about how they were stored.

Pure: no Flask, no ORM, no clock, no database.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import PayCadence
from app.utils.money import MONTHS_PER_YEAR
from app.services.recurrence._months import MONTH_SPANNING_UNITS
from app.services.recurrence._vocabulary import modelled_pattern

#: A seven-day pay cadence, used ONLY for its yearly count.
#:
#: The WEEK unit fires ``round(365.2425 / 7) = 52`` times a year -- the SAME
#: derivation :class:`~app.services.pay_calendar.PayCadence` applies to an
#: owner's own cadence, applied to a fixed seven days.  Deriving it rather than
#: writing ``52`` keeps ONE rule for "how often does something every N days
#: happen in a year"; plan step R8 is the WEEK unit's first writer and inherits
#: that rule without a second one being invented for it.
_WEEKLY = PayCadence(cadence_days=7)

#: How many times a year each CALENDAR unit fires, before its interval divides
#: the count.
#:
#: The twelve comes from :data:`app.utils.money.MONTHS_PER_YEAR`, the MONEY
#: constant, and not from ``recurrence._months``' integer twelve: this count is
#: a factor in a monthly equivalent, and an adversarial review of plan step
#: R7a-2b noted that one expression was reading its twelve from two modules.
#: ``_months``' is for month-ORDINAL arithmetic and stays where it is.
#:
#: ``PERIOD`` is absent DELIBERATELY, and the absence is not a lookup miss:
#: it is the one unit whose count is a property of the OWNER rather than of the
#: calendar, so :meth:`Cadence.occurrences_per_year` answers it from the pay
#: cadence BEFORE consulting this table.  An entry for it would have to be a
#: placeholder, which is how a per-owner fact becomes a constant again -- the
#: defect plan step R7a-2a removed.
_CALENDAR_UNITS_PER_YEAR: dict[RecurrenceUnitEnum, Decimal] = {
    RecurrenceUnitEnum.WEEK: _WEEKLY.periods_per_year,
    RecurrenceUnitEnum.MONTH: MONTHS_PER_YEAR,
    RecurrenceUnitEnum.YEAR: Decimal(1),
}

class RecurrenceFrequencyError(ShekelError):
    """A cadence names a unit with no yearly count.

    A broken invariant, not user input: every member of
    :class:`~app.enums.RecurrenceUnitEnum` must convert to a number of
    occurrences a year, because every monthly-equivalent figure in the
    application is that number over twelve.  A member added to the enum
    without one would otherwise contribute a silently wrong figure to the
    emergency-fund baseline and to every per-goal contribution floor.

    Raised rather than defaulted for the reason the whole redesign exists: a
    partial function over an enum is the defect being removed, and a plausible
    wrong number on a financial surface is worse than an error.  A
    :class:`~app.exceptions.ShekelError` like the other refusals this package
    makes, so a handler written against that hierarchy catches it.
    """


@dataclass(frozen=True)
class Cadence:
    """How often a recurrence fires: an interval and the unit it counts.

    The two-axis reading with the schedule-dependent half removed.  It is what
    :class:`~app.services.recurrence.ResolvedRecurrence` carries as its first
    two fields -- read back through
    :attr:`~app.services.recurrence.ResolvedRecurrence.cadence`, so the two are
    one value rather than two copies -- and what
    :func:`~app.services.recurrence.cadence_of` answers for a caller with no
    calendar to resolve against.

    Attributes:
        interval_n: How many *unit*\\ s pass between occurrences.  Always the
            two-axis reading: 3 for Quarterly, 6 for Semi-Annual, the authored
            count for ``Every N Periods``, 1 elsewhere.
        unit: The cadence unit *interval_n* counts.
    """

    interval_n: int
    unit: RecurrenceUnitEnum

    def units_per_year(self, pay_cadence: PayCadence) -> Decimal:
        """Return how many of this cadence's UNIT fit in a year, EXACTLY.

        The count before :attr:`interval_n` divides it -- 26 paychecks, 52
        weeks, 12 months, 1 year -- and always a whole number, which is what
        makes it the right thing to hand a money conversion.

        **The interval is deliberately NOT applied here, and that is an
        accuracy decision measured rather than assumed.** The obvious shape is
        to return :meth:`occurrences_per_year` and let the caller multiply, but
        that quotient is inexact for any interval that does not divide its
        unit's year -- ``52 / 12`` is ``4.333...`` -- so multiplying money by
        it rounds twice.  Measured: over 52,000,000 (cadence, interval, amount)
        combinations the two orders disagreed on **31,072 displayed cents**, and
        the lossy one was WRONG where they differed -- ``$0.18`` every 12 weekly
        paychecks is exactly ``$0.065`` a month, which it computes as
        ``0.06499...`` and rounds down to ``$0.06``.

        Handing back the exact count lets a caller divide ONCE, by the exact
        integer ``interval_n * 12``.  That form reproduces every one of the
        seven hand-written branches plan step R7a-2b replaced **at the
        displayed cent**, over 13,500,000 comparisons -- and it is provably so
        rather than merely swept: for ``a = A/100`` the exact value is
        ``A*U / (1200n)``, which is either exactly a half-cent (and then
        exactly representable, so both forms compute it exactly) or at least
        ``1/(1200n)`` away from one, while both forms' error is ~1e-27
        relative.  There is no amount at which they can round differently.

        **They are NOT equal unquantized, and an adversarial review corrected
        this docstring for claiming they were**: against the ``Every N
        Periods`` branch the last digits differ in about 21% of cases
        (``a=0.28, ppy=11, n=3`` gives ``...558`` against ``...556``), because
        that branch rounded twice and this divides once.  Where they differ
        this one is the correctly-rounded value.  The difference is ~1e-27 and
        both entry points round at their own boundary, so no published figure
        moves; the honest claim is "same cent", not "same digits".

        Args:
            pay_cadence: How often the owner is paid
                (:class:`~app.services.pay_calendar.PayCadence`).  Read only by
                the ``PERIOD`` unit; a calendar-space cadence needs no
                schedule, which is what lets a caller holding neither still ask
                about a monthly bill.

        Returns:
            The whole number of units in a year, as a ``Decimal``.

        Raises:
            RecurrenceFrequencyError: *unit* has no yearly count -- a member
                added to :class:`~app.enums.RecurrenceUnitEnum` without one.
        """
        if self.unit is RecurrenceUnitEnum.PERIOD:
            return pay_cadence.periods_per_year
        per_year = _CALENDAR_UNITS_PER_YEAR.get(self.unit)
        if per_year is None:
            raise RecurrenceFrequencyError(
                f"recurrence unit {self.unit!r} has no yearly count.  Every "
                f"member of RecurrenceUnitEnum must have one: a monthly "
                f"equivalent is that count over twelve, so a unit without one "
                f"would contribute a silently wrong figure to the "
                f"emergency-fund baseline and to every per-goal contribution "
                f"floor."
            )
        return per_year

    def occurrences_per_year(self, pay_cadence: PayCadence) -> Decimal:
        """Return how many times a year this cadence actually fires.

        :meth:`units_per_year` divided by the interval, and the one fact
        "how often is this" questions read.  Its live caller is
        ``calendar_service``'s infrequent-transaction badge, which asks
        whether a definition fires less often than monthly -- a COMPARISON,
        where the inexactness :meth:`units_per_year` documents cannot change
        an answer: the boundary cases divide evenly (26/2, 12/1) and a
        fractional one is nowhere near 12.

        **A money conversion must NOT use this**; see :meth:`units_per_year`
        for the 31,072 cents that says why.

        Args:
            pay_cadence: How often the owner is paid.

        Returns:
            The rate as an unquantized ``Decimal`` -- ``26`` for every
            paycheck on a biweekly schedule, ``4`` for quarterly, ``1`` for
            annual, ``26 / 3`` for every third paycheck.

        Raises:
            RecurrenceFrequencyError: See :meth:`units_per_year`.
        """
        return self.units_per_year(pay_cadence) / self.interval_n


@dataclass(frozen=True)
class PatternDerivation:
    """The two-axis reading of one closed-set pattern.

    Attributes:
        interval_n: The interval the pattern names, or ``None`` for
            ``Every N Periods``, which keeps the authored one -- it is the one
            pattern whose interval was already a column rather than a name.
        unit: The cadence unit the pattern counts in.
        placement: How its occurrences map onto pay periods.
    """

    interval_n: int | None
    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum


@dataclass(frozen=True)
class PatternReading:
    """What one STORED pattern says on both authored axes.

    The whole of what :func:`decode_pattern` recovers from the closed-set
    columns, held as one value so a caller cannot take the cadence and forget
    the placement -- two rules with the identical ``(1, MONTH)`` cadence differ
    only in it, and reading one without the other is how a bill that funds from
    the month's first paycheck comes to be treated as one that funds from the
    paycheck containing its own date.

    Attributes:
        cadence: How often the rule fires.
        placement: Which pay period an occurrence is funded from.
    """

    cadence: "Cadence"
    placement: PeriodPlacementEnum


@dataclass(frozen=True)
class EncodedPattern:
    """The closed-set columns one AUTHORED cadence is stored as.

    :func:`encode_cadence`'s whole answer.  Both fields together, because
    writing one without the other stores a cadence the decoder cannot recover:
    the pattern names the unit and the placement, and the column carries the
    interval for the single pattern whose interval was never in its name.

    Attributes:
        pattern: The ``RecurrencePatternEnum`` member to store, resolved to a
            ``ref.recurrence_patterns`` id by the write door.
        interval_n: What ``budget.recurrence_rules.interval_n`` must hold.  The
            authored interval for the one pattern that reads the column, and
            ``1`` for every other -- which is what every live row holds and
            what the column's ``CHECK (interval_n > 0)`` requires.  Writing the
            two-axis interval there instead would put a MONTH count in a column
            spelled "repeat every N pay periods", which nothing until plan step
            R7c can tell apart.
    """

    pattern: RecurrencePatternEnum
    interval_n: int


#: How each closed-set pattern reads on the two axes.
#:
#: Total over :class:`~app.enums.RecurrencePatternEnum`, and every entry names
#: a real cadence.  ``Once`` used to sit here holding INERT values copied from
#: ``Every Period`` -- byte-identical, so a consumer holding only a
#: :class:`~app.services.recurrence.ResolvedRecurrence` could not tell "does
#: not recur" from "every paycheck", and plan step R7c's downgrade could not
#: have round-tripped ``(1, period, containing_date)`` back to one pattern.
#: Plan step R2e-3 deleted the member instead (ruling R-R4 as amended by
#: R-R11): "does not recur" is ``recurrence_rule_id IS NULL``, which never
#: reaches a resolver.
PATTERN_DERIVATIONS: dict[RecurrencePatternEnum, PatternDerivation] = {
    RecurrencePatternEnum.EVERY_PERIOD: PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.PERIOD,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
    RecurrencePatternEnum.EVERY_N_PERIODS: PatternDerivation(
        interval_n=None, unit=RecurrenceUnitEnum.PERIOD,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
    RecurrencePatternEnum.MONTHLY: PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
    RecurrencePatternEnum.MONTHLY_FIRST: PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
    ),
    RecurrencePatternEnum.QUARTERLY: PatternDerivation(
        interval_n=3, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
    RecurrencePatternEnum.SEMI_ANNUAL: PatternDerivation(
        interval_n=6, unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
    RecurrencePatternEnum.ANNUAL: PatternDerivation(
        interval_n=1, unit=RecurrenceUnitEnum.YEAR,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
    ),
}


#: :data:`PATTERN_DERIVATIONS` read backwards: which pattern STORES a given
#: two-axis reading.
#:
#: INVERTED from the forward table rather than written out, so the encoder and
#: the decoder are one statement of the mapping read in two directions.  A
#: second hand-written table is the defect this arc exists to remove, and it
#: would fail in the direction nobody tests -- an entry changed on one side
#: only.
#:
#: The key's interval is the pattern's OWN, so ``Every N Periods`` keys on
#: ``None``: it is the one pattern that names no interval and takes the
#: authored one from a column.  :func:`encode_cadence` therefore looks for an
#: exact interval first and falls back to the ``None`` key, which is what makes
#: ``(1, PERIOD)`` encode as ``Every Period`` rather than as ``Every N
#: Periods`` with ``N = 1``.  Both would resolve identically; picking the named
#: one keeps plan step R7c's downgrade able to round-trip.
_PATTERNS_BY_READING: dict[
    tuple[int | None, RecurrenceUnitEnum, PeriodPlacementEnum],
    RecurrencePatternEnum,
] = {
    (derivation.interval_n, derivation.unit, derivation.placement): pattern
    for pattern, derivation in PATTERN_DERIVATIONS.items()
}


@dataclass(frozen=True)
class AuthorableCadence:
    """One reading the closed pattern set is able to STORE.

    :func:`authorable_cadences` returns these, and they are
    :data:`_PATTERNS_BY_READING`'s own keys rather than a description of them:
    the set a form may offer IS the set :func:`encode_cadence` can encode, so
    the two cannot be made to disagree by editing one.

    Attributes:
        interval_n: The interval this reading fixes, or ``None`` when ANY
            positive interval is storable -- true of exactly the one pattern
            that takes its interval from a column (``Every N Periods``).
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.
    """

    interval_n: int | None
    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum


def authorable_cadences() -> tuple[AuthorableCadence, ...]:
    """Return every ``(interval, unit, placement)`` the closed set can store.

    **The producer plan step R7b-2 serves the form's options from**, which is
    what makes :func:`encode_cadence`'s refusal unreachable rather than fenced.
    Until this existed the picker iterated ``RecurrencePatternEnum`` while the
    encoder read :data:`PATTERN_DERIVATIONS`, so "nothing offers an unstorable
    cadence" held only because the two sets happened to coincide -- see
    :func:`encode_cadence`, whose docstring names this step as the fix.

    **A placement is a property of the ``(unit, interval)`` pair, not of the
    unit**, and reading it as the latter is how a form comes to offer what
    cannot be stored.  ``MONTHLY_FIRST`` is ``(1, MONTH,
    PERIOD_STARTING_ON_OR_AFTER)`` and there is no quarterly or semi-annual
    twin, so "MONTH allows either placement" is true at interval 1 and false at
    3 and 6.  Returning the whole triple leaves that dependency in the data
    instead of asking a caller to rediscover it.

    Returns:
        One entry per storable reading, in
        :class:`~app.enums.RecurrencePatternEnum` declaration order -- which is
        most frequent first (paycheck, month, year), the order the picker has
        always rendered.

    """
    return tuple(
        AuthorableCadence(interval_n=interval_n, unit=unit, placement=placement)
        for interval_n, unit, placement in _PATTERNS_BY_READING
    )


def is_authorable(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> bool:
    """Return whether this reading can be STORED, without raising.

    :func:`encode_cadence`'s question asked by a validator rather than by a
    write door: the door refuses with an exception because reaching it with an
    unstorable cadence is a broken invariant, while a SUBMISSION carrying one
    is bad input to refuse with a field error.  Built on the same table, so the
    validator and the door cannot disagree about the set.

    Args:
        interval_n: The authored interval.
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.

    Returns:
        ``True`` when some closed-set pattern stores this reading.
    """
    if interval_n < 1:
        return False
    return (
        (interval_n, unit, placement) in _PATTERNS_BY_READING
        or (None, unit, placement) in _PATTERNS_BY_READING
    )


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




#: Families of anchor derivation.  Three, because the first occurrence is
#: measured in three different spaces -- the paycheck rhythm, the calendar, and
#: "each month's first paycheck".
#:
#: They were keyed on the closed pattern set until plan step R7b, which is what
#: made them a fourth column of a table this package is deleting.  The family is
#: a property of ``(unit, placement)``, and :func:`anchor_family` derives it.
#:
#: **They live HERE rather than beside the derivations they select, and plan
#: step R7b-2 moved them** (developer ruling 2026-08-13).  WHICH family a
#: cadence uses is a fact about the cadence and needs no schedule, which is this
#: module's whole charter; only WHERE the anchor lands needs one, and that stays
#: in ``_resolution``.  The move also gave the picker a public name to ask for
#: the day-of-month question rather than reaching into a sibling's privates.
FAMILY_PERIOD = "period"
FAMILY_CALENDAR = "calendar"
FAMILY_FIRST_OF_MONTH = "first_of_month"


def anchor_family(
    unit: RecurrenceUnitEnum, placement: PeriodPlacementEnum,
) -> str:
    """Return which anchor derivation a ``(unit, placement)`` cadence uses.

    **Total over the readings this package has a first occurrence for, and it
    REFUSES the rest rather than defaulting.**  Two are genuinely undefined
    today and each says which step defines it:

    * the ``WEEK`` unit anchors on an authored date this vocabulary does not
      yet collect -- plan step R8 is its first writer;
    * a cadence measured in YEARS but funded from a month's FIRST paycheck has
      no cycle month left to name.  ``_resolution._first_of_month_anchor``
      answers "the 1st of the first qualifying month", which for a yearly rule
      would fire in whichever month the schedule happened to open in.  Plan
      step R8 owns the placement axis (plan ledger row D20).

    Neither is reachable from a form: the picker's options are derived from
    :data:`PATTERN_DERIVATIONS`, which is a SUBSET of what this function
    accepts, so an unhandled reading here is a broken invariant rather than
    user input.

    Package-internal rather than underscore-private for the reason
    :func:`pattern_member` is: ``_resolution`` dispatches on it and
    :func:`fires_on_day_of_month` projects it, so the two read one
    implementation.

    Args:
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.

    Returns:
        One of the ``FAMILY_*`` constants.

    Raises:
        RecurrenceResolutionError: When the pair has no anchor derivation.
    """
    if unit is RecurrenceUnitEnum.PERIOD:
        # The placement is inert for the ANCHOR (see ``RecurrenceSpec``), so it
        # is deliberately not part of the condition: branching on a distinction
        # that makes no difference is how a second answer starts.
        #
        # **It is not inert for STORAGE**, and an adversarial review of plan
        # step R7b-1 caught this reading as too broad: the closed pattern set
        # has no name for a pay-period cadence funded from a LATER paycheck, so
        # :func:`encode_cadence` refuses the pair that this function accepts.
        # Nothing authors it -- no writer passes a placement with the PERIOD
        # unit -- and the two answers are about different questions, so they
        # are not in conflict; plan step R7c gives the pair a column and the
        # asymmetry goes with the closed set.
        return FAMILY_PERIOD
    if unit in MONTH_SPANNING_UNITS:
        if placement is PeriodPlacementEnum.CONTAINING_DATE:
            return FAMILY_CALENDAR
        # **Both conditions are NAMED, and an adversarial review of plan step
        # R7b-1 is why.**  This read ``if unit is MONTH`` alone, which is an
        # implicit ``else`` over the placement axis: plan step R8 adds a third
        # member (fund in ADVANCE, plan ledger row D20), and a MONTH rule
        # carrying it would have fallen in here and been anchored on the 1st of
        # a qualifying month -- silently, with no refusal, placing money in the
        # wrong paycheck.  ``_describe._placement_note`` refuses an unworded
        # placement for exactly this reason one module over; a derivation that
        # decides where money moves may not be laxer than the label.
        if (
            placement is PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
            and unit is RecurrenceUnitEnum.MONTH
        ):
            return FAMILY_FIRST_OF_MONTH
    raise RecurrenceResolutionError(
        f"a recurrence of unit {unit!r} funded {placement!r} has no first "
        f"occurrence this resolver can derive.  The WEEK unit and a "
        f"year-scale cadence deferred onto a month's first paycheck are both "
        f"plan step R8's; refusing here rather than defaulting is what stops "
        f"a rule firing on a month the cadence never names."
    )


def fires_on_day_of_month(
    unit: RecurrenceUnitEnum, placement: PeriodPlacementEnum,
) -> bool:
    """Return whether a cadence's occurrences land on a DAY of the month.

    The projection of :func:`anchor_family` the recurrence FORM takes, and one
    implementation is what stops the picker and the anchor derivation from
    drifting about which cadences have a day-of-month coordinate.

    It is NOT "is this unit measured in months", and an adversarial review of
    plan step R7b-2 is why the distinction is stated here.  ``MONTHLY_FIRST``
    is a MONTH-unit cadence that anchors on a month's first PAYCHECK, so
    ``day_of_month`` is never read for it and ``_resolution._month_anchor_day``
    records no nominal day -- which is why the form has always hidden the Day
    of Month input for it.  Asking the router keeps that a property of the
    derivation rather than a second list beside it: a placement added at plan
    step R8 changes both answers at once, or neither.

    Args:
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.

    Returns:
        ``True`` when the pair anchors on the calendar, which is the one
        family that reads ``spec.day_of_month`` and ``spec.month_of_year``.

    Raises:
        RecurrenceResolutionError: When the pair has no anchor derivation at
            all -- see :func:`anchor_family`.  Raised rather than answered
            ``False`` because a form that renders no day input for a cadence
            whose anchor cannot be derived is showing a control set for a rule
            it could not save.
    """
    return anchor_family(unit, placement) == FAMILY_CALENDAR


def pattern_member(pattern_id: int) -> RecurrencePatternEnum:
    """Return the enum member *pattern_id* names, or RAISE.

    **Package-internal rather than underscore-private, and plan step R7a-2b is
    why**: both this module's :func:`cadence_of` and ``_resolution.resolve``
    read a pattern's membership, so the lookup has one implementation across
    the two.  It stays out of the package's public surface -- this module is
    private and ``__init__`` does not re-export it -- so the name is visible to
    siblings and to nothing else.

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




def require_positive_interval(interval_n: int, where: str) -> None:
    """Refuse a cadence interval that is not positive.

    Package-internal for the reason :func:`pattern_member` is: the decoder, the
    encoder and ``resolve`` all need the rule and must not each carry their
    own.

    **The check is on the AUTHORED value, not on a pattern's own**, and the
    difference is a live defect an adversarial review measured.  Every calendar
    pattern's interval is a hard-coded 1, 3 or 6, which can never be
    non-positive -- so checking that looked at nothing, while the write door
    wrote the authored value into a ``NOT NULL`` column carrying
    ``CHECK (interval_n > 0)``.  An authored 0 therefore reached the flush as an
    unhandled ``IntegrityError``.

    Args:
        interval_n: The AUTHORED interval.
        where: What to name in the refusal -- the pattern id a stored rule
            carries, or the ``(unit, placement)`` an authoring request states.
            The caller composes it because only the caller knows which
            vocabulary the offending value arrived in.

    Raises:
        RecurrenceResolutionError: When *interval_n* is not positive.  Mirrors
            ``ck_recurrence_rules_positive_interval``, refused here so the
            caller sees the offending value rather than an ``IntegrityError``
            at flush, and so the phase modulo cannot divide by zero.
    """
    if interval_n < 1:
        raise RecurrenceResolutionError(
            f"recurrence interval_n must be positive, got {interval_n} for "
            f"{where}.  It is written to a NOT NULL column with "
            f"CHECK (interval_n > 0), so letting it through would raise an "
            f"unhandled IntegrityError at the flush instead of here."
        )


def decode_pattern(pattern_id: int, interval_n: int) -> PatternReading:
    """Return what a STORED closed-set pattern says on the authored axes.

    **The DECODER half of the seam plan step R7b put between the two
    vocabularies.**  ``budget.recurrence_rules`` still stores a
    ``ref.recurrence_patterns`` id; a caller states, and every reader below
    this line works in, ``(interval_n, unit, placement)``.  This is the one
    place the first becomes the second, :func:`encode_cadence` is the one place
    the reverse happens, and both read :data:`PATTERN_DERIVATIONS` -- so the
    round trip is one table read twice rather than two tables that agree.
    Plan step R7c deletes both together with the columns.

    Args:
        pattern_id: A ``ref.recurrence_patterns`` id.
        interval_n: The stored ``budget.recurrence_rules.interval_n``.  Read
            only for the one pattern whose interval is a column
            (``Every N Periods``); every other pattern's interval is a property
            of the pattern, which is why a hidden form input landing on a
            Quarterly rule's column cannot make it read as monthly.

    Returns:
        The :class:`PatternReading`.

    Raises:
        RecurrenceResolutionError: *pattern_id* names no modelled pattern, or
            the stored interval is not positive.  **A rule whose pattern this
            application does not model is REFUSED rather than answered** (ruled
            2026-08-11) -- one disposition for one state, the one
            :func:`resolve` has always had.  ``amount_to_monthly`` used to
            answer ``None`` here and its caller dropped the template, so a
            single such rule 500'd the Recurring surface through ``read_rule``
            while ``/savings`` silently left the obligation out of its
            emergency-fund baseline: the same row, counted on one page and not
            the other.
    """
    pattern = pattern_member(pattern_id)
    derivation = PATTERN_DERIVATIONS[pattern]
    require_positive_interval(interval_n, f"pattern id {pattern_id}")
    return PatternReading(
        cadence=Cadence(
            interval_n=(
                interval_n if derivation.interval_n is None
                else derivation.interval_n
            ),
            unit=derivation.unit,
        ),
        placement=derivation.placement,
    )


def encode_cadence(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> EncodedPattern:
    """Return the closed-set columns an AUTHORED cadence is stored as.

    **The ENCODER half of the seam** -- see :func:`decode_pattern` for the
    other, and for why both read one table.

    **Not every authorable cadence has a pattern, and that is the whole reason
    plan step R7c exists.**  ``(2, MONTH)`` and ``(1, WEEK)`` are perfectly
    well-defined and the resolver walks them correctly; the closed set has no
    NAME for them, so until R7c makes ``unit_id`` and ``interval_n`` authored
    columns they cannot be STORED.  The refusal here is that gap stated once,
    and it disappears with the table.

    **It is UNREACHABLE through the form since plan step R7b-2**, and the
    history is worth keeping because an adversarial review corrected an earlier
    draft of this paragraph for claiming that too soon: the picker's options
    came from ``pattern_choices``, which iterated ``RecurrencePatternEnum``
    rather than this table, so "nothing offers such a cadence" was true only
    because the two sets happened to coincide, protected by no gate.
    :func:`authorable_cadences` inverts this table and
    ``_picker.cadence_options`` words it, so the offer set IS the storable set;
    what still reaches this refusal is a hand-crafted POST, and
    ``_helpers.validate_authorable_cadence`` turns that into a field error one
    layer up.

    Args:
        interval_n: How many *unit*\\ s pass between occurrences.
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.

    Returns:
        The :class:`EncodedPattern` the write door assigns.

    Raises:
        RecurrenceResolutionError: When *interval_n* is not positive, or when
            no closed-set pattern names this reading.
    """
    require_positive_interval(interval_n, f"cadence ({unit!r}, {placement!r})")
    # The pattern that names this exact interval first, then the one that takes
    # its interval from a column.  See :data:`_PATTERNS_BY_READING` for why the
    # order matters: ``(1, PERIOD)`` must encode as ``Every Period``.
    pattern = _PATTERNS_BY_READING.get((interval_n, unit, placement))
    if pattern is not None:
        return EncodedPattern(pattern=pattern, interval_n=1)
    pattern = _PATTERNS_BY_READING.get((None, unit, placement))
    if pattern is not None:
        return EncodedPattern(pattern=pattern, interval_n=interval_n)
    raise RecurrenceResolutionError(
        f"no recurrence pattern stores a cadence of every {interval_n} "
        f"{unit!r} funded {placement!r}.  ``budget.recurrence_rules`` names "
        f"its cadence with a closed pattern set until plan step R7c gives it "
        f"an authored unit and interval, so this reading has nowhere to be "
        f"written -- and nothing offers it, because the picker's own options "
        f"are derived from the same table."
    )


def cadence_of(pattern_id: int, interval_n: int) -> Cadence:
    """Return how often a stored pattern fires, with NO schedule involved.

    The projection of :func:`decode_pattern` taken by the consumers that ask
    "how often" and never "when" or "which paycheck":
    ``obligations_aggregator``'s monthly equivalent and ``calendar_service``'s
    infrequent-transaction badge, neither of which holds a
    :class:`~app.services.pay_calendar.PayCalendar`.  That absence is exactly
    why both read ``pattern_id`` through a hand-written table until plan step
    R7a-2b; this module's own docstring records the one caller that DOES hold a
    calendar, an adversarial review having corrected "nor do their callers".

    **It is the third public entry point in the STORED vocabulary**, and plan
    step R7c retires all three together: its two callers above read
    ``rule.pattern_id`` off the row, so they change with the columns even
    though neither is part of the form work R7b-2 and R7b-4 delete.

    Args:
        pattern_id: A ``ref.recurrence_patterns`` id.
        interval_n: The stored interval; see :func:`decode_pattern`.

    Returns:
        The :class:`Cadence`.

    Raises:
        RecurrenceResolutionError: See :func:`decode_pattern`.
    """
    return decode_pattern(pattern_id, interval_n).cadence


__all__ = [
    "FAMILY_CALENDAR",
    "FAMILY_FIRST_OF_MONTH",
    "FAMILY_PERIOD",
    "PATTERN_DERIVATIONS",
    "AuthorableCadence",
    "Cadence",
    "EncodedPattern",
    "PatternDerivation",
    "PatternReading",
    "RecurrenceFrequencyError",
    "RecurrenceResolutionError",
    "anchor_family",
    "authorable_cadences",
    "cadence_of",
    "decode_pattern",
    "encode_cadence",
    "fires_on_day_of_month",
    "is_authorable",
]
