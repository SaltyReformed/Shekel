"""
Shekel Budget App -- Recurrence Engine: WHICH periods a rule names

The gating and occurrence-matching preamble both write paths share
(:func:`resolve_generation_plan`), the two value types it answers in, and the
rule-and-period derivation of a generated row's own date
(:func:`compute_due_date`).

Nothing here writes.  This leaf answers "where does this definition fire, and
on what day", which is the question ``_generate`` and ``_maintain`` both have
to ask before they can act, and asking it in one place is what stops the two
from drifting on which periods a rule applies to.
"""
import calendar as cal
import logging
from datetime import date
from typing import NamedTuple

from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import StatusEnum
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence import rule_occurrences, scheduling_day_of_month
from app.services._recurrence_common import check_scenario_ownership

logger = logging.getLogger(__name__)



class PlannedOccurrence(NamedTuple):
    """One occurrence a generate pass WILL write, and the paycheck it lands in.

    The NARROWED twin of
    :class:`~app.services.recurrence.OccurrencePlacement`, and the narrowing is
    now its whole reason to exist.  The walk answers about every occurrence of
    a rule, so its ``period`` is optional -- ``None`` where the saved schedule
    does not reach that far.  This value is what survives
    :func:`resolve_generation_plan`'s three filters (a period exists, it is in
    the pass's write window, it is not before the caller's bound), so its
    ``period`` is never absent and no consumer needs to ask.  A type that says
    so is the only place that fact can be stated once.

    **It carried an ORM row until pay-calendar plan step C2-f3c**, and THAT was
    the difference between the two types before the narrowing became it: a
    ``Transaction`` is written against ``budget.pay_periods.id``, so the id was
    resolved back to a row here rather than in each engine's write loop.  The
    derived period carries the id, the payday and the last covered day -- every
    field the seam ever read off the row -- so the round trip through the ORM
    bought nothing and cost the seam a second read of the schedule.

    **The occurrence is carried rather than re-derived** (plan step R4b-2).
    Until this step generation answered in periods alone, so the two things
    that need the date -- the gap report (plan ledger row **D7**) and the
    repeat refusal's message (**D19**) -- either could not have it or would
    have had to walk the cadence a second time, which is the redundant-producer
    shape this arc removes everywhere else.

    Attributes:
        occurrence: The date the rule's cadence names.  For the ``PERIOD``
            unit this is the paycheck's own payday; see
            :mod:`app.services.recurrence._occurrence`.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
            generated record lives in.  Always inside the pass's write window,
            always materialised (so its ``period_id`` is a real
            ``budget.pay_periods.id``), and never ``None``.  **The write loops
            read this and not *occurrence*,** because a row's date still comes
            from ``compute_due_date`` (plan ledger row D18, owned by plan step
            R5); the occurrence is what the repeat refusal names.
    """

    occurrence: date
    period: DerivedPeriod




class GenerationPlan(NamedTuple):
    """Resolved inputs a recurrence generate pass needs after gating.

    Returned by :func:`resolve_generation_plan` once the cross-user
    ownership check and the rule-present gating have passed, so the caller
    can proceed straight to model-specific row creation.  Public (no
    leading underscore) because it is the return contract of the public
    :func:`resolve_generation_plan`, which the transfer engine consumes.

    Attributes:
        rule: The template's recurrence rule, already confirmed present.
        placements: One :class:`PlannedOccurrence` per occurrence this pass
            may write, ascending by occurrence date.  **A pay period can
            appear more than once** -- at a pay cadence of 30 days or more a
            monthly bill legitimately falls inside one paycheck several times
            -- and since plan step **R17** re-keyed the unique index onto the
            occurrence, both rows STORE.  It was refused until then (plan
            ledger row D19), because the index held one row per paycheck.
        projected_id: The ``Projected`` status id every generated row carries.

    **It carried a fourth field until plan step C2-b2**, ``gaps`` -- the
    occurrence dates the owner's schedule had no pay period for, which the
    write path reported and the read-only predictor ignored.  A pay period's
    end is now DERIVED from the next payday, so the periods tile and a hole
    between two of them is not a state a READER can see; the field, the report
    and the ``PlacementOutcome`` that fed it went with it (plan ledger rows
    **D7** / **P27**, recurrence **R-F10**).  A legacy hole is ABSORBED by the
    preceding paycheck and reported by ``integrity_check`` **BA-07** instead --
    and it is one of THREE shapes where the derivation and the stored columns
    disagree, all of which move money and only one of which BA-07 sees.  The
    other two are the stored cadence against the last stored end (row **P28**)
    and a stored ordinal that is not ``0..n-1`` (row **P26**);
    ``recurrence/_occurrence.py``'s module docstring holds the full statement.
    """

    rule: RecurrenceRule
    placements: tuple[PlannedOccurrence, ...]
    projected_id: int




def resolve_generation_plan(
    template, schedule, scenario_id, effective_from, *, block_message,
):
    """Run the shared gating + occurrence-matching preamble for a generate pass.

    Both this module's ``generate_for_template`` and the transfer
    engine's identical preamble (``app/services/transfer_recurrence.py``)
    perform the same steps before their model-specific row creation: the
    cross-user ownership check, the rule-present gating, and the occurrence
    walk against the owner's schedule.  Centralising them guarantees the two
    engines cannot drift on which periods a rule applies to.  Public (no
    leading underscore) because the transfer engine calls it cross-module --
    the shared preamble is deliberately part of this module's public surface,
    like :func:`rule_occurrences`.

    **It answers in ``(occurrence, period)`` pairs** (plan step R4b-2).  It
    used to answer in periods alone, so the date a row's cadence actually
    named was computed, used to select a paycheck, and then thrown away --
    leaving the repeat refusal of the day able to say only how MANY times a
    definition fell inside one paycheck, and leaving an occurrence in a
    schedule gap indistinguishable from one that was never generated.

    **The rule is resolved against the whole schedule and the answer is then
    NARROWED to the window** (plan step R4b), in that order.  Doing it the
    other way round is what defect D22 was: resolving against the window makes
    the window's own first payday look like the owner's, so a ``Monthly First``
    rule re-fires in a month it already covered.  Narrowing afterwards keeps
    the window a window -- without it an extend would re-walk every historical
    period and the pass would cost O(schedule) writes instead of O(new).

    **The two ``effective_from`` defaults this used to apply are gone**, and
    deleting them is a simplification rather than a behaviour change.  It fell
    back to the rule's start period and then to the first candidate period;
    both are already inside the anchor
    (``app.services.recurrence._resolution._effective_start`` takes the
    GREATEST of the schedule's opening payday, the rule's ``start_date`` and
    its start period's).

    **The reason is about the PLACED PERIOD, not the occurrence**, and an
    adversarial review corrected an earlier wording that said "no walk emits an
    occurrence before the anchor".  That is false for the ``PERIOD`` unit:
    ``_occurrence._period_walk`` yields a qualifying paycheck's own payday,
    which precedes a mid-period anchor deliberately (ruling R-R8).  What holds
    for every unit is the thing the old filter actually tested -- it bounded
    the placed period's ``end_date``, and every period any walk can yield
    satisfies ``end_date >= anchor >= effective_from``.  So neither default
    could ever drop a row the anchor had not already dropped.  Verified by
    measurement as well as by argument: identical answers for all 46 live rules
    over all 61 production periods, and a byte-identical
    ``tests/oracles/recurrence_baseline.txt`` over the 428 shapes it then
    held (430 since plan step R4b-2 added D10's).  ``None`` now
    plainly means "no lower window bound".

    Args:
        template: The (Transaction|Transfer)Template to generate from.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            their whole pay calendar plus the ids of the periods this pass may
            write into.
        scenario_id: The scenario to generate into.
        effective_from: Optional lower bound on the window; occurrences whose
            placed period ENDS before it are dropped.  ``None`` applies no
            bound.  It is the CALLER's display / regeneration boundary and
            never the rule's own -- conflating the two is how defect D2
            happened.
        block_message: Cross-user-block log message distinguishing the
            calling engine.

    Returns:
        A :class:`GenerationPlan` when generation should proceed, or
        ``None`` when ownership fails or the rule is absent (every
        caller returns an empty list in the None case).
    """
    if not check_scenario_ownership(
        logger, template, scenario_id, block_message=block_message,
    ):
        return None

    rule = template.recurrence_rule
    if rule is None:
        # No recurrence rule -- nothing to generate.  This is the ONE way a
        # definition says "does not recur" (plan step R2e-3 retired the
        # ``Once`` pattern that was the second way, and the guard that read
        # it).
        return None

    # Narrow the walk's answer to what this pass may actually write.  Dropping
    # the window intersection would make a schedule extend re-walk every
    # historical period and cost O(schedule) writes instead of O(new).
    #
    # **The bound is applied to the DERIVED end, and so is the row select this
    # call runs beside** (pay-calendar plan step C2-f3c).  It used to be
    # applied to the ORM row's STORED ``end_date``, deliberately, because
    # ``_maintain.regenerate_for_template``'s sweep was SQL over that same
    # column -- and reading the bound off two different definitions of "when
    # does this paycheck end" is how a row gets selected for maintenance but
    # never NAMED by the rule, which since plan step R10-a means RETIRED rather
    # than merely recreated.  ``_recurrence_common.rows_this_pass_may_maintain``
    # now selects on a period-id set filtered by the same derived end this line
    # reads, off the same calendar, so the two halves are one predicate rather
    # than two that have to agree.  Plan step C4 drops the column both used to
    # read.
    window = schedule.write_period_ids
    placements = []
    for placement in rule_occurrences(rule, schedule.calendar):
        period = placement.period
        if period is None:
            # The saved schedule does not reach this occurrence.  Ordinary --
            # the next schedule extend places it -- and since plan step C2-b2
            # it is the only way to get a placement with no period.
            continue
        if period.period_id not in window:
            continue
        if effective_from is not None and period.end_date < effective_from:
            continue
        placements.append(PlannedOccurrence(placement.occurrence, period))
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    return GenerationPlan(rule, tuple(placements), projected_id)




def compute_due_date(rule, period):
    """Compute the due_date for a generated transaction.

    Derives the calendar date the bill is actually due, using the
    recurrence rule's scheduling day and optional due-day override.
    Public (no leading underscore): the transfer engine, the transfers
    preview route and a data migration all derive a row's due date through this
    same pure helper, so it is deliberately part of this module's public
    surface (like :func:`rule_occurrences`) rather than a leading-underscore
    internal.

    Source priority:
      1. rule.due_day_of_month (if set and differs from the scheduling day)
      2. the rule's SCHEDULING DAY (placed within the period's month context)
      3. period.start_date (for a cadence that names no day of the month)

    **The scheduling day is DERIVED rather than read off a column since plan
    step R7c-c** (developer ruling 2026-08-16, plan ledger row **D37**).  It was
    ``rule.day_of_month``, which the write door encoded from the rule's authored
    columns and that step drops;
    :func:`~app.services.recurrence.scheduling_day_of_month` answers the same
    value from the columns that survive, and was measured equal to the stored
    one for all 46 live rules on a production clone before the column went.
    Both it and this function are deleted by plan step **R5**, which gives a
    generated row its own ``occurs_on`` and ``due_on``.

    Next-month convention: if due_day_of_month < the scheduling day, the due
    date falls in the following calendar month.  Example: a rule scheduled on
    the 22nd with due_day_of_month=1 means the bill is due on the 1st of the
    next month after the scheduling month.

    Month-end clamping: day values exceeding the month's last day are
    clamped (e.g. day 31 in April becomes 30, day 30 in Feb becomes 28).

    Args:
        rule: The RecurrenceRule to date the row from.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` the
            transaction was assigned to.  It reads that period's payday and its
            last covered day; both are DERIVED from the owner's payday set
            since pay-calendar plan step C2-f3c, where they were the stored
            columns plan step **C4** drops.  Measured equal on production the
            same day: 62 periods, zero disagreements.

    Returns:
        A date object representing the due date.

    Raises:
        RecurrenceResolutionError: When the rule names a unit or a placement
            this application does not model -- see
            :func:`~app.services.recurrence.scheduling_day_of_month`.  It could
            not raise while it read a plain column; it now makes the same
            refusal every other reader of this rule already makes, rather than
            dating a row from a cadence nothing can read.
    """
    dom = scheduling_day_of_month(rule)
    due_dom = rule.due_day_of_month

    # A cadence that names no day of the month -- every-paycheck, every-N, and
    # a monthly rule funded from the month's first paycheck -- is dated from
    # its period's start.
    if dom is None:
        return period.start_date

    # Determine the base month by finding which month within the period
    # contains the scheduling-day target.  This is the LAST reader of the
    # endpoint-month scan plan step R4a deleted from period selection, and it
    # carries the same defect: at a cadence where the firing month is neither
    # endpoint the row is dated in the wrong month entirely (plan ledger row
    # D18).  Plan step R5 owns it, with the due-date model it rewrites.
    base_year = period.start_date.year
    base_month = period.start_date.month

    for dt in (period.start_date, period.end_date):
        last_day = cal.monthrange(dt.year, dt.month)[1]
        target_day = min(dom, last_day)
        target = date(dt.year, dt.month, target_day)
        if period.start_date <= target <= period.end_date:
            base_year = dt.year
            base_month = dt.month
            break

    if due_dom is None or due_dom == dom:
        # No separate due date -- use day_of_month in the base month.
        last_day = cal.monthrange(base_year, base_month)[1]
        return date(base_year, base_month, min(dom, last_day))

    # Next-month convention: due_day_of_month < day_of_month means the
    # due date falls in the month after the scheduling month.
    if due_dom < dom:
        if base_month == 12:
            due_year = base_year + 1
            due_month = 1
        else:
            due_year = base_year
            due_month = base_month + 1
    else:
        due_year = base_year
        due_month = base_month

    last_day = cal.monthrange(due_year, due_month)[1]
    return date(due_year, due_month, min(due_dom, last_day))
