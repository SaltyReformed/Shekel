"""
Shekel Budget App -- Reading a stored recurrence rule (plan step R4b-2)

The door a written recurrence is READ through, symmetric with
:mod:`app.services.recurrence._authoring`, which is the door one is written
through.  One composition and its projections:

* :func:`recurrence_spec` -- a rule row's authored state, back out as the
  :class:`~app.services.recurrence.RecurrenceSpec` that authored it.  The write
  door's partial-change idiom is built on it (read the spec, replace one fact
  with ``dataclasses.replace``, re-author the whole value), which is why it is
  a READ living beside the other reads rather than inside the writer.
* :func:`read_rule` -- **THE composition**: resolve the row against the
  owner's schedule, then walk and place its occurrences, keeping BOTH halves.
* :func:`resolved_recurrence` -- the first half alone, for a caller that wants
  what the rule MEANS and not where its rows land.
* :func:`rule_occurrences` -- the second half alone, the shape three surfaces
  and the frozen baseline have always taken.
* :func:`placed_periods` -- the projection three surfaces take of that answer.

**One caller needs both halves, and that is why :func:`read_rule` exists**
(plan step R7a).  The Recurring surface resolves every rule to date its "Next"
column and now also to describe its cadence; composing the two steps at the
call site would put the resolve-then-place sequence in two places, so the
composition lives here once and the page takes the value whole.  Nothing is
computed and discarded: a caller that wants only the meaning
(:func:`resolved_recurrence`) never walks an occurrence.

Four surfaces ask :func:`rule_occurrences` the same question, and they must not
be able to disagree:

* ``recurrence_engine.resolve_generation_plan``, the generation seam both
  engines share, which turns the answer into rows;
* ``recurring_view``, whose next-date column must name the date the grid cell
  it points at will carry;
* ``routes._recurrence_preview``, the form's live "next five occurrences"
  fragment, which must show what saving would produce;
* ``tests.oracles.recurrence_baseline``, the frozen behaviour snapshot every
  step of the redesign is measured against.

**This replaced ``recurrence_engine.match_periods``** (plan step R4b-2).  That
adapter answered in PERIODS and applied a caller's lower window bound itself,
so two facts were computed and thrown away: the occurrence DATE, which the
repeat refusal needed to name (plan ledger row D19), and WHY an occurrence had
no pay period (row D7).  Both are in the answer now.  The lower bound went back
to the callers that have one, because it is a display / regeneration boundary
rather than a property of the recurrence, and conflating those two is what
defect D2 was.

It lives here rather than in ``recurrence_engine`` because it writes nothing:
that module carries the session, the models and the row-creation state machine,
and three of the four callers above want none of it.  It lives here rather than
in ``_occurrence`` because it takes an ORM row, and ``_occurrence`` is pure by
contract.

Flask-isolated and read-only: it touches no session and issues no query -- the
owner's schedule arrives as a
:class:`~app.services.pay_calendar.PayCalendar` the caller already holds.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import DerivedPeriod, PayCalendar
from app.services.recurrence._occurrence import (
    OccurrencePlacement,
    occurrence_placements,
)
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    decode_pattern,
)
from app.services.recurrence._resolution import (
    RecurrenceSpec,
    ResolvedRecurrence,
    resolve,
)


@dataclass(frozen=True)
class RuleReading:
    """One stored rule, read against its owner's schedule.

    Both halves of :func:`read_rule`'s answer, held together so a caller that
    needs each of them asks once.

    Attributes:
        resolved: What the rule MEANS on the two axes, or ``None`` when the
            owner has no pay periods -- see :func:`resolved_recurrence` for why
            that one refusal is answered rather than raised.
        placements: Every ``(occurrence, pay period)`` pair the rule names
            through the schedule's horizon; empty when *resolved* is ``None``,
            because a schedule with no periods can host nothing.
    """

    resolved: ResolvedRecurrence | None
    placements: tuple[OccurrencePlacement, ...]

    def __post_init__(self) -> None:
        """Refuse a value whose two halves disagree.

        A rule that could not be resolved named no occurrence, so placements
        without a meaning is a value that contradicts itself.  A check rather
        than a docstring guarantee, for the reason
        :class:`~app.services.recurrence.OccurrencePlacement` records in its
        own: this project has been burned by an invariant the generated
        ``__init__`` did not enforce.

        Raises:
            RecurrenceResolutionError: When there are placements but no
                resolved meaning.
        """
        if self.resolved is None and self.placements:
            raise RecurrenceResolutionError(
                f"a rule reading carries {len(self.placements)} placement(s) "
                f"with no resolved meaning.  A recurrence that could not be "
                f"resolved names no occurrence, so the pair disagrees with "
                f"itself and a caller filtering on one field would read the "
                f"other."
            )


def recurrence_spec(rule: RecurrenceRule) -> RecurrenceSpec:
    """Read a rule's authored state back out as a spec.

    The inverse of authoring, and what makes a partial change expressible
    without a partial write: a caller that owns ONE fact about a rule reads
    the spec, replaces that fact, and re-authors the whole value.

    **The DECODE step for a stored ROW is here** (plan step R7b).  The row
    still names its cadence with a closed pattern set;
    :func:`~app.services.recurrence._frequency.decode_pattern` turns
    ``(pattern_id, interval_n)`` back into the ``(interval_n, unit,
    placement)`` a caller authored.  Its inverse is the write door's
    ``encode_cadence``, and both read one table, so the round trip is one
    statement of the mapping rather than two that agree.  Plan step R7c deletes
    this call together with the columns.

    **It is not the only ``decode_pattern`` call in the application**, and an
    adversarial review corrected an earlier "here and nowhere else" for saying
    so: the two form doors and the preview each decode a POSTED id until plan
    step R7b-2 replaces the picker.  What is single is the MAPPING, not the
    call site.

    Args:
        rule: The rule to read.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` that authored it.
        **Round-trips exactly**, and ``test_recurrence_frequency`` proves it
        over every authorable cadence rather than by argument: encoding maps a
        calendar cadence's interval into the pattern's NAME and writes ``1`` to
        the column, so decoding reads the interval back off the name.

    Raises:
        RecurrenceResolutionError: When the row names a pattern this
            application does not model, or carries a non-positive interval --
            see ``decode_pattern``.  A caller that is REPLACING the cadence
            must take :func:`recurrence_spec_with_cadence` instead, which reads
            no cadence and therefore cannot fail on one.
    """
    reading = decode_pattern(rule.pattern_id, rule.interval_n)
    return recurrence_spec_with_cadence(
        rule,
        interval_n=reading.cadence.interval_n,
        unit=reading.cadence.unit,
        placement=reading.placement,
    )


def recurrence_spec_with_cadence(
    rule: RecurrenceRule,
    *,
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> RecurrenceSpec:
    """Read a rule's authored state with a STATED cadence in place of its own.

    The read door for the one caller that OWNS the cadence: the edit form,
    whose whole job is to state one.  :func:`recurrence_spec` is this function
    plus a decode, so "everything about a rule except how often it repeats" has
    one implementation rather than two that agree.

    **Its existence is a defect the two-axis swap would otherwise have
    introduced, measured against ``origin/dev``.**  The edit form offers a
    stored pattern the application no longer models as a trailing option and
    tells the user to pick a new one before saving (``pattern_choices_for``,
    ``UNAVAILABLE_PATTERN_MESSAGE``) -- so picking a new one IS the documented
    repair.  Routing that repair through :func:`recurrence_spec` made it read
    the broken cadence on the way to replacing it, and the read raised: the one
    action the surface tells the user to take became a 500.  Reading no cadence
    is what makes the repair structural rather than excepted.

    Args:
        rule: The rule to read.  Its ``pattern_id`` and ``interval_n`` are NOT
            consulted.
        interval_n: The cadence interval to state.
        unit: The cadence unit to state.
        placement: The placement to state.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.
    """
    return RecurrenceSpec(
        user_id=rule.user_id,
        unit=unit,
        interval_n=interval_n,
        placement=placement,
        offset_periods=rule.offset_periods,
        day_of_month=rule.day_of_month,
        due_day_of_month=rule.due_day_of_month,
        month_of_year=rule.month_of_year,
        start_period_id=rule.start_period_id,
        start_date=rule.start_date,
        end_date=rule.end_date,
        max_occurrences=rule.max_occurrences,
    )


def resolved_recurrence(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> ResolvedRecurrence | None:
    """Return what *rule* MEANS against its owner's schedule.

    The first of :func:`read_rule`'s two steps, exposed on its own for the
    callers that want the cadence and not its rows -- the Recurring surface's
    archived drawer describes every archived definition and places none.

    Composes the two readers in the module docstring's order: the rule's
    authored state (:func:`recurrence_spec`, the same reader the write door's
    partial-change idiom uses), resolved against the owner's schedule.

    **This is where the empty-schedule refusal is answered rather than
    raised**, and only this one.
    :func:`~app.services.recurrence.resolve` refuses an owner with no pay
    periods -- rightly, since registration bootstraps one and an owner with
    none is a broken invariant -- but the Recurring surface renders every
    definition a user has, and taking a whole page to a 500 for a state no
    rule of THIS rule's is wrong about would be the fence rather than the fix.
    The other four refusals ``resolve`` makes are about the rule ITSELF and
    must not be swallowed with it, which is why this is a guard on one
    condition rather than a short-circuit before the call.  Finding F-10's
    ruling is what closes the question of whether an empty schedule should
    exist at all.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule, which the rule's first
            occurrence is measured against.

    Returns:
        The :class:`~app.services.recurrence.ResolvedRecurrence`, or ``None``
        when the owner's schedule holds no pay periods.

    Raises:
        RecurrenceResolutionError: When the rule cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, a
            day / month outside its column's domain, or a rule paired with
            another user's schedule.
    """
    if not calendar.periods:
        return None
    return resolve(recurrence_spec(rule), calendar)


def read_rule(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> RuleReading:
    """Read *rule* against its owner's schedule, keeping both halves.

    **The composition, held in one place** (plan step R7a): resolve, then walk
    the cadence forward and place each occurrence.  A caller that needs the
    meaning AND the placements -- the Recurring surface, which describes each
    definition's cadence and dates its next occurrence -- takes this rather
    than performing the two steps itself, so the sequence exists once.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule.

    Returns:
        The :class:`RuleReading`.

    Raises:
        RecurrenceResolutionError: See :func:`resolved_recurrence`.
        RecurrenceGenerationError: See :func:`rule_occurrences`.
    """
    resolved = resolved_recurrence(rule, calendar)
    if resolved is None:
        return RuleReading(resolved=None, placements=())
    return RuleReading(
        resolved=resolved,
        placements=occurrence_placements(resolved, calendar),
    )


def rule_occurrences(
    rule: RecurrenceRule, calendar: PayCalendar,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence *rule* names, each with the pay period it lands in.

    The placement half of :func:`read_rule`, and the shape the generation seam,
    the form preview and the frozen baseline have taken since plan step R4b-2.

    **The rule's own window is applied, and a caller cannot bypass it.**
    ``start_date`` binds through the anchor
    :func:`~app.services.recurrence.resolve` derives and ``end_date`` through
    the occurrence engine's stopping bound (ruling R-R6), so a loan payment's
    ``start_date`` still guarantees no installment is generated before the loan
    originates (plan step C9a).  Before plan step R4a both bounds filtered
    candidate PERIODS instead -- ``end_date`` against a period's START -- which
    generated rows dated outside the window the user set (defect D5).

    **A period can appear TWICE, and that is the honest answer.**  At a pay
    cadence of 30 days or more a monthly bill legitimately occurs several times
    inside one paycheck; the reverse matcher walked PAYCHECKS and so silently
    emitted one row for three months of rent (defect D3).
    ``budget.transactions`` cannot yet HOLD the separate rows --
    ``idx_transactions_template_period_scenario`` is unique over
    ``(template, period, scenario)`` -- so the WRITE path refuses
    (``_recurrence_common.refuse_unstorable_repeats``), and plan step R5 re-keys
    the index and lifts the refusal.  This function reports what the cadence
    NAMES; it does not decide what is storable, which is why the read-only
    surfaces still render the repeats.

    **An occurrence with no pay period is REPORTED, and since plan step C2-b2
    that means ONE thing**: the saved schedule does not reach it, which is every
    schedule's ordinary tail rather than a signal.  The other reading -- a
    schedule HOLE, plan ledger row D7 -- went unconstructible when the calendar
    began deriving each period's end from the next payday, and the
    ``PlacementOutcome`` that told the two apart went with it.

    Args:
        rule: The stored (or transient) recurrence rule.
        calendar: The OWNER's WHOLE pay-period schedule, which the rule's first
            occurrence is measured against.  A subset resolves the rule against
            a pay history the owner does not have -- plan ledger rows D22, D25
            and D2, all measured live on production, all closed at plan step
            R4b-1 by making the schedule a value
            (:class:`~app.services.generation_schedule.GenerationSchedule`).

    Returns:
        One :class:`~app.services.recurrence.OccurrencePlacement` per
        occurrence through the schedule's horizon, ascending by occurrence
        date.  The resolver's own value type rather than ORM rows: this is a
        pure question about a schedule, and the one caller that must WRITE a
        row already holds the owner's rows to map ``period_id`` back onto.

    Raises:
        RecurrenceResolutionError: When the rule cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, a
            day / month outside its column's domain, or a rule paired with
            another user's schedule.  The reverse matcher answered ``[]`` for
            the first of those and ``ValueError`` for the third; both now name
            the offending value.  **An EMPTY schedule is the one refusal this
            answers as an empty tuple** -- see :func:`resolved_recurrence`,
            which holds that guard.
        RecurrenceGenerationError: When the resolved value names something the
            occurrence engine cannot walk -- a business-day shift (plan step R8
            is its first author) or a placement with no rule.  Unreachable from
            any value ``resolve`` can produce today, and stated so a later step
            that makes it reachable finds the contract written down.
    """
    return read_rule(rule, calendar).placements


def placed_periods(
    placements: Iterable[OccurrencePlacement],
    *,
    ending_on_or_after: date | None = None,
) -> list[DerivedPeriod]:
    """Project *placements* onto the pay periods a caller can show or write.

    The projection three surfaces take of :func:`rule_occurrences` -- the
    Recurring surface's next-date column, the form's occurrence preview, and
    the frozen baseline oracle -- held once so they cannot come to filter
    differently.  It is exactly what the retired ``match_periods`` adapter
    returned, which is also why the baseline blob did not move when the adapter
    went.

    The generation seam does NOT use it: that path needs the
    ``(occurrence, period)`` pair and the write window, so it walks the
    placements itself.

    Args:
        placements: The answer from :func:`rule_occurrences`.
        ending_on_or_after: Drop periods ENDING before this date.  ``None``
            (the default) applies no bound.  It is the CALLER's display or
            regeneration boundary and never the rule's own -- the rule's
            opening bound is its anchor, and conflating the two is what defect
            D2 was.  **The end it compares is the DERIVED one** (plan step
            C2-b2), so on a schedule whose stored column disagrees this can
            keep or drop a period the stored value would not -- see
            ``recurrence/_occurrence.py``'s module docstring for the three
            shapes.  Every caller of this projection is a DISPLAY surface; the
            generation seam applies its own bound to the ORM row it resolves,
            for exactly that reason (``recurrence_engine``'s
            ``resolve_generation_plan``), because that bound also has to agree
            with an SQL sweep over the stored column.

    Returns:
        The placed periods, ascending by occurrence date, one entry per
        occurrence and therefore possibly repeating.
    """
    return [
        placement.period
        for placement in placements
        if placement.period is not None
        and (
            ending_on_or_after is None
            or placement.period.end_date >= ending_on_or_after
        )
    ]


__all__ = [
    "RuleReading",
    "placed_periods",
    "read_rule",
    "recurrence_spec",
    "recurrence_spec_with_cadence",
    "resolved_recurrence",
    "rule_occurrences",
]
