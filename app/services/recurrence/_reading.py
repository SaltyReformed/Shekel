"""
Shekel Budget App -- Reading a stored recurrence rule (plan step R4b-2)

The door a written recurrence is READ through, symmetric with
:mod:`app.services.recurrence._authoring`, which is the door one is written
through.  Two functions and one projection:

* :func:`recurrence_spec` -- a rule row's authored state, back out as the
  :class:`~app.services.recurrence.RecurrenceSpec` that authored it.  The write
  door's partial-change idiom is built on it (read the spec, replace one fact
  with ``dataclasses.replace``, re-author the whole value), which is why it is
  a READ living beside the other reads rather than inside the writer.
* :func:`rule_occurrences` -- every ``(occurrence, pay period)`` pair the rule
  names against the owner's schedule.
* :func:`placed_periods` -- the projection three surfaces take of that answer.

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
:class:`~app.services.recurrence._calendar.PeriodCalendar` the caller already
holds.
"""
from collections.abc import Iterable
from datetime import date

from app.models.recurrence_rule import RecurrenceRule
from app.services.recurrence._calendar import PeriodCalendar, SchedulePeriod
from app.services.recurrence._occurrence import (
    OccurrencePlacement,
    occurrence_placements,
)
from app.services.recurrence._resolution import RecurrenceSpec, resolve


def recurrence_spec(rule: RecurrenceRule) -> RecurrenceSpec:
    """Read a rule's authored state back out as a spec.

    The inverse of authoring, and what makes a partial change expressible
    without a partial write: a caller that owns ONE fact about a rule reads
    the spec, replaces that fact, and re-authors the whole value.

    Args:
        rule: The rule to read.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` that authored it.
        Round-trips exactly -- resolution ignores ``interval_n`` for every
        pattern but ``Every N Periods`` (where the stored value IS the
        authored one), and re-derives ``offset_periods`` from the start period
        whenever the rule names one.
    """
    return RecurrenceSpec(
        user_id=rule.user_id,
        pattern_id=rule.pattern_id,
        interval_n=rule.interval_n,
        offset_periods=rule.offset_periods,
        day_of_month=rule.day_of_month,
        due_day_of_month=rule.due_day_of_month,
        month_of_year=rule.month_of_year,
        start_period_id=rule.start_period_id,
        start_date=rule.start_date,
        end_date=rule.end_date,
        max_occurrences=rule.max_occurrences,
    )


def rule_occurrences(
    rule: RecurrenceRule, calendar: PeriodCalendar,
) -> tuple[OccurrencePlacement, ...]:
    """Return every occurrence *rule* names, each with the pay period it lands in.

    Composes the three steps in the module docstring's order: read the rule's
    authored state (:func:`recurrence_spec`, the same reader the write door's
    partial-change idiom uses), resolve it against the owner's schedule, then
    walk the cadence forward and place each occurrence.

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

    **An occurrence with no pay period is REPORTED, and it says which of the
    two "no period" answers it is** (:class:`PlacementOutcome`).  A
    ``SCHEDULE_GAP`` is owed with nowhere to live (plan ledger row D7) and the
    generation seam logs it; ``BEYOND_THE_SCHEDULE`` is every schedule's
    ordinary tail and is silent.  Conflating them was a defect in this step's
    first draft and a neutral review measured it at 43% of biweekly schedule
    openings -- see :class:`PlacementOutcome`.

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
            re-raises as an empty answer** -- see below.
        RecurrenceGenerationError: When the resolved value names something the
            occurrence engine cannot walk -- a business-day shift (plan step R8
            is its first author) or a placement with no rule.  Unreachable from
            any value ``resolve`` can produce today, and stated so a later step
            that makes it reachable finds the contract written down.
    """
    # An empty schedule has no period to match and no anchor to measure.
    # ``resolve`` refuses it -- rightly, since registration bootstraps a period
    # and an owner with none is a broken invariant -- but the Recurring surface
    # renders every definition a user has, and taking a whole page to a 500 for
    # a state no rule of THIS rule's is wrong about would be the fence rather
    # than the fix.  Refused HERE and only here: the other four refusals
    # ``resolve`` makes are about the rule itself and must not be swallowed
    # with it, which is why this is a guard on one condition rather than a
    # short-circuit before the call.  Finding F-10's ruling is what closes the
    # question of whether an empty schedule should exist at all.
    if not calendar.periods:
        return ()

    return occurrence_placements(
        resolve(recurrence_spec(rule), calendar), calendar,
    )


def placed_periods(
    placements: Iterable[OccurrencePlacement],
    *,
    ending_on_or_after: date | None = None,
) -> list[SchedulePeriod]:
    """Project *placements* onto the pay periods a caller can show or write.

    The projection three surfaces take of :func:`rule_occurrences` -- the
    Recurring surface's next-date column, the form's occurrence preview, and
    the frozen baseline oracle -- held once so they cannot come to filter
    differently.  It is exactly what the retired ``match_periods`` adapter
    returned, which is also why the baseline blob did not move when the adapter
    went.

    The generation seam does NOT use it: that path needs the
    ``(occurrence, period)`` pair, the write window, and the gap report, so it
    walks the placements itself.

    Args:
        placements: The answer from :func:`rule_occurrences`.
        ending_on_or_after: Drop periods ENDING before this date.  ``None``
            (the default) applies no bound.  It is the CALLER's display or
            regeneration boundary and never the rule's own -- the rule's
            opening bound is its anchor, and conflating the two is what defect
            D2 was.

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


__all__ = ["placed_periods", "recurrence_spec", "rule_occurrences"]
