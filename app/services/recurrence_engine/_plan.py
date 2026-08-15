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

from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import StatusEnum
from app.services.recurrence import rule_occurrences
from app.services._recurrence_common import check_scenario_ownership

logger = logging.getLogger(__name__)



class PlannedOccurrence(NamedTuple):
    """One occurrence a generate pass will write, and the row it writes into.

    The engine-side twin of
    :class:`~app.services.recurrence.OccurrencePlacement`, and it exists
    because the two halves of a generated row come from different places: the
    occurrence DATE is a pure fact about the rule and the schedule, while the
    pay period has to be the caller's own ORM row -- that is what a
    ``Transaction`` / ``Transfer`` is written against, and what the paycheck
    calculator reads.  Resolving the id back to the row happens ONCE, in
    :func:`resolve_generation_plan`, rather than in each engine's write loop.

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
        period: The owner's :class:`~app.models.pay_period.PayPeriod` row the
            generated record lives in.  Always inside the pass's write window,
            and never ``None``: an occurrence the schedule cannot host is
            reported and dropped by :func:`resolve_generation_plan` before a
            plan is built.  **The write loops read this and not *occurrence*,**
            because a row's date still comes from ``compute_due_date`` (plan
            ledger row D18, owned by plan step R5); the occurrence is what the
            repeat refusal names and what the gap report skips.
    """

    occurrence: date
    period: PayPeriod




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
            -- which is what :func:`refuse_unstorable_repeats` refuses while
            ``idx_transactions_template_period_scenario`` is keyed on the
            paycheck (plan ledger row D19).
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
    leaving :func:`refuse_unstorable_repeats` able to say only how MANY times a
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
            their whole pay-period schedule plus the window this pass may
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

    # The occurrence walk answers in DerivedPeriod values; row creation needs
    # the ORM rows.  ``write_periods`` is keyed on ``pay_periods.id``, so the
    # single lookup below does BOTH jobs -- narrow to the window, and hand back
    # the row to write into.  Dropping that intersection would make a schedule
    # extend re-walk every historical period and cost O(schedule) writes
    # instead of O(new).
    #
    # **The ORM row is resolved BEFORE the bound is applied, and the ORDER is
    # load-bearing** (found by adversarial review of plan step C2-b2).
    # ``effective_from`` also bounds the ROW SELECT that
    # ``_maintain.regenerate_for_template`` runs beside this call, and that
    # select is SQL over ``pay_periods.end_date`` -- the STORED column
    # (``_recurrence_common.query_rows_from_effective_date``).  Testing
    # ``placement.period.end_date`` here would test the DERIVED end instead, so
    # on a schedule where the two disagree the two halves would consider
    # different periods: a row selected but never NAMED where the derived end
    # is earlier, and a stale amount surviving an edit where it is later.
    # Reading the bound off the same column the select reads makes the two one
    # statement again.  **This said "the DELETE sweep ... runs before calling
    # here" until plan step R10-a**, which is no longer the shape: that pass
    # maintains rows rather than deleting them, and resolves this plan first
    # rather than afterwards.  The hazard is unchanged and its consequence is
    # now WORSE -- an unnamed row is RETIRED rather than merely recreated.
    # Plan step C4 deletes that column, at which point the select has to move
    # onto the calendar and this comment with it.
    window = schedule.write_periods
    placements = []
    for placement in rule_occurrences(rule, schedule.calendar):
        if placement.period is None:
            # The saved schedule does not reach this occurrence.  Ordinary --
            # the next schedule extend places it -- and since plan step C2-b2
            # it is the only way to get a placement with no period.
            continue
        period = window.get(placement.period.period_id)
        if period is None:
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
    preview route, the due-date backfill script, and a data migration all
    derive a row's due date through this same pure helper, so it is
    deliberately part of this module's public surface (like
    :func:`rule_occurrences`) rather than a leading-underscore internal.

    Source priority:
      1. rule.due_day_of_month (if set and differs from day_of_month)
      2. rule.day_of_month (placed within the period's month context)
      3. period.start_date (for every-paycheck patterns with no day)

    Next-month convention: if due_day_of_month < day_of_month, the due
    date falls in the following calendar month.  Example: day_of_month=22
    with due_day_of_month=1 means the bill is due on the 1st of the
    next month after the scheduling month.

    Month-end clamping: day values exceeding the month's last day are
    clamped (e.g. day 31 in April becomes 30, day 30 in Feb becomes 28).

    Args:
        rule: The RecurrenceRule with day_of_month and due_day_of_month.
        period: The PayPeriod the transaction was assigned to.

    Returns:
        A date object representing the due date.
    """
    dom = rule.day_of_month
    due_dom = rule.due_day_of_month

    # Patterns without day_of_month (every-paycheck, every-N): use period start.
    if dom is None:
        return period.start_date

    # Determine the base month by finding which month within the period
    # contains the day_of_month target.  This is the LAST reader of the
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
