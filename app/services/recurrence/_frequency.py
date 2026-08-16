"""What a recurrence's cadence IS, with no schedule to measure it against.

Plan step **R7a-2b**.  :func:`app.services.recurrence.resolve` answers what a
recurrence MEANS against one owner's pay calendar, and most of that answer does
not need the calendar at all: the interval, the unit and the placement are what
the rule itself states, and only the anchor -- where the first occurrence lands
-- needs a schedule.  This module is that schedule-free half, split out so a
consumer holding no calendar can still ask how often something repeats.

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

**The dependency runs ONE way** -- ``_resolution`` imports this module and this
module imports nothing of it -- which is what keeps the split a boundary rather
than a pair of files that need each other.

**The SEAM between two vocabularies LEFT at plan step R7c-c, and it left
because there is only one vocabulary now.**  ``budget.recurrence_rules`` stated
its cadence with a closed set of eight pattern names until then, so this module
carried the translation: ``PATTERN_DERIVATIONS`` and its computed inverse,
``encode_cadence`` (authored -> stored) and ``decode_pattern`` (stored ->
authored), plus ``stored_interval`` and ``cadence_of`` reading through them.
``interval_n`` and ``unit_id`` are authored columns from that step, so a
caller's ``(interval_n, unit, placement)`` IS what the table holds and there is
nothing to translate.  All seven are deleted; :class:`Cadence` -- the pair
itself -- :meth:`Cadence.occurrences_per_year` and the family router survive,
being facts about the two axes rather than about how they were stored.

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

**The router is also what the OFFER SET is derived from now**
(:func:`authorable_cadences`), and that is the same property plan step R7b-2
gave the form by serving its options from the ENCODER's table: the set a form
may offer IS the set the application can resolve, so an unresolvable cadence is
unofferable rather than fenced.  What changed at R7c-c is only which producer
holds that set -- the encoder's table was the binding constraint while a cadence
had to have a NAME, and once every ``(interval, unit, placement)`` can be
stored, what is left is whether a first occurrence can be derived for it.

Pure: no Flask, no ORM, no clock, no database.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import PayCadence
from app.utils.money import MONTHS_PER_YEAR
from app.services.recurrence._months import (
    MONTH_SPANNING_UNITS,
    months_per_step,
)
from app.services.recurrence._vocabulary import (
    modelled_placement,
    modelled_unit,
)

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
class CadenceReading:
    """What one STORED rule says on both authored axes.

    The whole of what a rule's cadence columns say, held as one value so a
    caller cannot take the cadence and forget the placement -- two rules with
    the identical ``(1, MONTH)`` cadence differ only in it, and reading one
    without the other is how a bill that funds from the month's first paycheck
    comes to be treated as one that funds from the paycheck containing its own
    date.

    **It was ``PatternReading`` until plan step R7c-c**, when it stopped being a
    reading OF a pattern: it was what ``decode_pattern`` recovered from
    ``pattern_id`` plus the ``interval_n`` column, and it is now what
    ``unit_id`` / ``placement_id`` / ``interval_n`` say directly.  The value's
    shape did not move and neither did any consumer's use of it; the name did,
    because a name that says "pattern" would be the closed set's last surviving
    claim on a vocabulary it no longer holds.

    Attributes:
        cadence: How often the rule fires.
        placement: Which pay period an occurrence is funded from.
    """

    cadence: "Cadence"
    placement: PeriodPlacementEnum


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

    Neither is reachable from a form: the picker's options are
    :func:`authorable_cadences`, which is exactly the set this function DOES
    answer for -- it is derived by asking this function, so the offer set
    cannot drift from it.  A raise reaching a form would be a broken invariant
    rather than user input.

    **That derivation is why the raise is also ordinary control flow**, and the
    two readings have to be held apart: :func:`authorable_cadences` CATCHES it
    on every pair it probes, so "this pair has no anchor" is a normal answer
    there and a broken invariant everywhere else.  It is not a defect signal
    per se; it is one for any caller that did not ask the offer set first.

    Package-internal rather than underscore-private because ``_resolution``
    dispatches on it and :func:`fires_on_day_of_month` projects it, so the two
    read one implementation.

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
        # **The asymmetry an adversarial review of plan step R7b-1 recorded
        # here is GONE, and plan step R7c-c is where it went.**  This function
        # accepted the pair while ``encode_cadence`` refused it, because the
        # closed pattern set had no name for a pay-period cadence funded from a
        # LATER paycheck -- two answers about two different questions, in
        # conflict only if a reader took them for one.  With the closed set
        # dropped the pair is storable, and what keeps it UNOFFERED is
        # :func:`emits_period_starts`, which states the real reason: both
        # placements carry a payday back to its own paycheck, so the choice
        # would change nothing.
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


@dataclass(frozen=True)
class AuthorableCadence:
    """One ``(unit, placement)`` pair a rule may be authored on.

    :func:`authorable_cadences` returns these.

    **It lost its ``interval_n`` field at plan step R7c-c**, and the loss is the
    step: the interval was ``None`` for the one pattern that took it from a
    column and a fixed 1 / 3 / 6 for the rest, so a "storable reading" had to
    carry it.  Every positive interval is storable now, for every unit, so the
    interval is no longer part of what an offer NAMES -- which is also why the
    form's month ``<select>`` becomes a free number box in the same step.

    **The consequence for plan ledger row D32**: a placement was a property of
    the ``(unit, interval)`` PAIR while ``MONTHLY_FIRST`` had no quarterly twin,
    so raising a monthly rule's interval silently rewrote its funding choice.
    With the closed set gone the MONTH unit offers both placements at every
    interval, and the pair dependency is gone with the fusion that created it.

    Attributes:
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.
    """

    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum


def emits_period_starts(unit: RecurrenceUnitEnum) -> bool:
    """Return whether *unit*'s occurrences ARE pay-period start dates.

    **The one place "the placement is inert" is stated**, and it is what keeps
    :func:`authorable_cadences` from offering a control that changes nothing.
    ``_occurrence._period_walk`` yields a qualifying paycheck's own
    ``start_date``, and both members of
    :class:`~app.enums.PeriodPlacementEnum` carry a period start back to that
    same period -- the one that CONTAINS it, and the first one STARTING on or
    after it, are the same period when the date is a period's own start.  So a
    pay-period cadence has one funding answer however the placement reads, and
    a form offering the choice would be asking the user to decide something the
    engine ignores.

    Every other unit emits a calendar DATE, which can fall strictly inside a
    period, where the two placements genuinely differ.

    Stated as a predicate over the unit rather than derived from
    :func:`anchor_family`, because the two are different questions and only
    happen to agree here: the family says where the FIRST occurrence lands, and
    this says whether the placement can move a LATER one.  Pinned by driving
    ``place`` over a whole schedule under both placements
    (``test_recurrence_occurrence``), which is a proof over the schedule rather
    than the argument above.

    Args:
        unit: The cadence unit.

    Returns:
        ``True`` for the ``PERIOD`` unit and ``False`` for every other.
    """
    return unit is RecurrenceUnitEnum.PERIOD


def authorable_cadences() -> tuple[AuthorableCadence, ...]:
    """Return every ``(unit, placement)`` pair a rule may be authored on.

    **The producer the form serves its options from**, which is what makes an
    unresolvable cadence unofferable rather than fenced behind a refusal.  Plan
    step R7b-2 gave the picker that property by deriving its options from the
    ENCODER's table; plan step R7c-c keeps it and changes only which producer
    is the binding constraint, because with ``unit_id`` and ``interval_n``
    authored columns every reading can be STORED and what is left is whether a
    first occurrence can be DERIVED for it.

    Two rules, each derived rather than listed:

    * :func:`anchor_family` must answer.  It refuses the ``WEEK`` unit, whose
      anchor this vocabulary does not yet collect, and a YEAR-scale cadence
      deferred onto a month's first paycheck, which has no cycle month left to
      name; both are plan step R8's, and both drop out of the router rather
      than out of a second list beside it.
    * The placement must be able to CHANGE the answer
      (:func:`emits_period_starts`).  A pay-period cadence's occurrences are
      paydays and both placements carry a payday back to its own paycheck, so
      offering the choice would render a control the engine ignores.

    The INTERVAL is not part of an offer.  Every positive interval is authorable
    for every unit here, which is what the form's free number box renders and
    what ``ck_recurrence_rules_positive_interval`` is the whole of the domain
    for.

    Returns:
        One entry per authorable pair, in
        :class:`~app.enums.RecurrenceUnitEnum` declaration order -- most
        frequent first (paycheck, month, year), the order the picker has always
        rendered -- and within a unit in
        :class:`~app.enums.PeriodPlacementEnum` declaration order.
    """
    offered = []
    for unit in RecurrenceUnitEnum:
        for placement in PeriodPlacementEnum:
            try:
                anchor_family(unit, placement)
            except RecurrenceResolutionError:
                continue
            offered.append(AuthorableCadence(unit=unit, placement=placement))
            if emits_period_starts(unit):
                break
    return tuple(offered)


def is_authorable(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> bool:
    """Return whether this reading can be AUTHORED, without raising.

    The write door's question asked by a validator rather than by the door: the
    door raises because reaching it with an unauthorable cadence is a broken
    invariant, while a SUBMISSION carrying one is bad input to refuse with a
    field error.  Built on :func:`authorable_cadences`, so the validator and
    the form's own offer set cannot disagree about the set.

    Args:
        interval_n: The authored interval.
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.

    Returns:
        ``True`` when a rule may be authored on this reading.
    """
    if interval_n < 1:
        return False
    return AuthorableCadence(unit=unit, placement=placement) in (
        authorable_cadences()
    )


def require_authorable_cadence(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    where: str,
) -> None:
    """Refuse a cadence this application cannot author.

    **The write door's completeness refusal, and plan step R7c-c is why it has
    a name of its own.**  ``encode_cadence`` used to be it: a cadence the
    closed pattern set could not NAME had nowhere to be written, so the door
    raised and every caller above it inherited the refusal -- including the
    recurrence PREVIEW, which builds a transient rule through the same door and
    whose whole contract is to show what saving would produce.  Deleting the
    encoder took that refusal with it, and the preview began listing dates for
    a cadence the schema then refused -- measured, five of them for the ``WEEK``
    unit.

    So the refusal is restated where it belongs, over the set the FORM offers
    rather than over the set a storage encoding could name.  It is
    :func:`is_authorable`'s raising twin and reads the same producer, which is
    what keeps the three answers one: the picker offers it, the schema accepts
    it, the door writes it.

    Args:
        interval_n: The authored interval.
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.
        where: What to name in the refusal, composed by the caller because only
            the caller knows which value is being built.

    Raises:
        RecurrenceResolutionError: When *interval_n* is not positive, or when
            the pair is one this application cannot author.
    """
    require_positive_interval(interval_n, where)
    if is_authorable(interval_n, unit, placement):
        return
    raise RecurrenceResolutionError(
        f"a recurrence of every {interval_n} {unit!r} funded {placement!r} is "
        f"not one this application can author, for {where}.  Either the pair "
        f"has no first-occurrence derivation (the WEEK unit and a year-scale "
        f"cadence deferred onto a month's first paycheck are both plan step "
        f"R8's), or the placement is inert for the unit and offering it would "
        f"store a choice the edit form cannot preselect.  The offer set is "
        f"``authorable_cadences``; nothing the picker renders reaches here."
    )


def unit_member(unit_id: int) -> RecurrenceUnitEnum:
    """Return the cadence unit a STORED ``unit_id`` names, or RAISE.

    The read door takes a rule's unit off ``budget.recurrence_rules.unit_id``
    from plan step R7c-b, and the column is ``NOT NULL`` with an
    ``ondelete="RESTRICT"`` FK to ``ref.recurrence_units`` -- so an id no enum
    member names is a ``ref`` row this application does not model, which is a
    broken invariant rather than user input.  It RAISES because a rule read
    with a fabricated cadence is worse than a refused read.

    It was ``pattern_member``'s twin until plan step R7c-c, which dropped
    ``pattern_id`` and deleted that function; this one and
    :func:`placement_member` are what is left of the pair's job.

    Built on :func:`~app.services.recurrence.modelled_unit`, which owns the
    lookup, so the SUBMISSION door (which answers ``None`` and flashes) and this
    one can never disagree about which units the application models.

    Args:
        unit_id: A stored ``ref.recurrence_units`` id.

    Returns:
        The matching :class:`~app.enums.RecurrenceUnitEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *unit_id*.
    """
    member = modelled_unit(unit_id)
    if member is not None:
        return member
    raise RecurrenceResolutionError(
        f"recurrence unit id {unit_id} matches no RecurrenceUnitEnum member.  "
        f"budget.recurrence_rules.unit_id is NOT NULL with a RESTRICT foreign "
        f"key, so a stored id this application does not model means the ref "
        f"seed and the enum have diverged; deriving a cadence from it would "
        f"generate rows on a rhythm the rule never named."
    )


def placement_member(placement_id: int) -> PeriodPlacementEnum:
    """Return the placement a STORED ``placement_id`` names, or RAISE.

    :func:`unit_member`'s twin on the second authored axis; see it for why a
    stored id is raised on rather than answered.

    Args:
        placement_id: A stored ``ref.period_placements`` id.

    Returns:
        The matching :class:`~app.enums.PeriodPlacementEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *placement_id*.
    """
    member = modelled_placement(placement_id)
    if member is not None:
        return member
    raise RecurrenceResolutionError(
        f"recurrence placement id {placement_id} matches no "
        f"PeriodPlacementEnum member.  budget.recurrence_rules.placement_id is "
        f"NOT NULL with a RESTRICT foreign key, so a stored id this "
        f"application does not model means the ref seed and the enum have "
        f"diverged; it decides WHICH PAYCHECK PAYS a bill, so defaulting it "
        f"would move real money."
    )


def require_positive_interval(interval_n: int, where: str) -> None:
    """Refuse a cadence interval that is not positive.

    Package-internal, and named rather than inlined into its one caller
    (:func:`require_authorable_cadence`) because a SECOND function depends on
    it having run: :func:`canonical_cadence`'s floor division answers ``0`` for
    a non-positive interval, and it says so in its own ``Args``.  A rule two
    functions rely on is one neither may state for itself.

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


def canonical_cadence(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> Cadence:
    """Return the ONE spelling of the cadence *interval_n* *unit* names.

    **Ruling R-R17, applied at the write door** (plan step R7c-c).  Freeing the
    interval makes ``(12, MONTH)`` authorable, and it is the same rhythm as
    ``(1, YEAR)``: the occurrence walk strides twelve months either way
    (:func:`~app.services.recurrence._months.months_per_step`), the anchor
    family is the same, and both fire once a year.  Two spellings of one rhythm
    is the second vocabulary this arc removed from the table, arriving back
    through the form -- the Recurring surface would word the same annual bill
    "Every 12 months" on one row and "Yearly" on another, and the obligations
    filter would group them apart.

    **The substitution is guarded on the ANCHOR FAMILY, not asserted for every
    placement**, and the guard is load-bearing rather than defensive.
    ``(12, MONTH, PERIOD_STARTING_ON_OR_AFTER)`` is a real cadence -- twelve
    months apart, funded from the month's first paycheck -- and
    :func:`anchor_family` has no derivation for its YEAR spelling (a year-scale
    cadence deferred onto a month's first paycheck names no cycle month; plan
    step R8 owns it).  Rewriting it would turn an authorable cadence into a
    refusal at the door that was about to store it.

    The stride is read through ``months_per_step`` rather than compared against
    a literal twelve, so this function states no month arithmetic of its own --
    the same rule ``_occurrence`` follows for the walk it seeds.

    Args:
        interval_n: The authored interval.  Assumed positive; the write door
            has already run :func:`require_positive_interval`, and this
            function's floor division would otherwise answer ``0``.
        unit: The authored cadence unit.
        placement: Which pay period funds an occurrence.  Read only to check
            that both spellings resolve the same way.

    Returns:
        The canonical :class:`Cadence` -- the caller's own pair for every
        cadence but a whole number of years authored in months.
    """
    stated = Cadence(interval_n=interval_n, unit=unit)
    if unit is not RecurrenceUnitEnum.MONTH:
        return stated
    months_per_year = months_per_step(RecurrenceUnitEnum.YEAR, 1)
    if months_per_step(unit, interval_n) % months_per_year != 0:
        return stated
    try:
        coarser = anchor_family(RecurrenceUnitEnum.YEAR, placement)
    except RecurrenceResolutionError:
        return stated
    if coarser != anchor_family(unit, placement):
        return stated
    return Cadence(
        interval_n=interval_n // months_per_year,
        unit=RecurrenceUnitEnum.YEAR,
    )


__all__ = [
    "FAMILY_CALENDAR",
    "FAMILY_FIRST_OF_MONTH",
    "FAMILY_PERIOD",
    "AuthorableCadence",
    "Cadence",
    "CadenceReading",
    "RecurrenceFrequencyError",
    "RecurrenceResolutionError",
    "anchor_family",
    "authorable_cadences",
    "canonical_cadence",
    "emits_period_starts",
    "fires_on_day_of_month",
    "is_authorable",
    "require_authorable_cadence",
]
