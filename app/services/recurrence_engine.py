"""
Shekel Budget App -- Recurrence Engine

Given a transaction template and its recurrence rule, generates Transaction
entries into the appropriate future pay periods.

Implements the full state machine from §4.8:
  - Respects is_override and is_deleted flags.
  - Returns conflicts (overridden/deleted) for the route layer to present
    to the user as prompts.
  - Never touches done/received/credit transactions.

**Which periods a rule fires in is no longer decided here.**  This module used
to carry five ``_match_*`` helpers that scanned candidate periods and asked
whether each contained the rule's target day; plan step R4a deleted them and
made an adapter, ``match_periods``, a thin wrapper over the forward occurrence
engine (:mod:`app.services.recurrence`), which walks the rule's own cadence and
then places each occurrence on a pay period.  **Plan step R4b-2 deleted the
adapter too**: ``recurrence.rule_occurrences`` answers in
``(occurrence, period)`` pairs, generation carries the pair as far as the write
loop, and an occurrence the schedule cannot host is REPORTED rather than
dropped where nobody looks (plan ledger row **D7**).  A generated row's own
DATE is still derived from its period by :func:`compute_due_date`, not from the
occurrence -- that is plan ledger row **D18**, and plan step R5 owns it with the
``due_date`` -> ``occurs_on`` split.  What survives here is the GENERATION half: gating,
the per-period skip predicate, amount resolution, row creation, and the
regenerate / conflict state machine.

**And the schedule it is read against is the OWNER's, not the caller's**
(plan step R4b).  Every entry point below takes a
:class:`~app.services.generation_schedule.GenerationSchedule`: the owner's whole
pay-period schedule, plus the window this pass may write into.  The two used to
be one ``periods`` argument, so a caller handing over a SUBSET -- which the
schedule-extend path does on every run -- silently re-read every rule against
that subset.  That class of defect is measured in ``GenerationSchedule``'s own
docstring; the shape here is simply that a window narrows what is WRITTEN and
never what a recurrence MEANS.

What a definition can say it repeats by is
:class:`~app.enums.RecurrencePatternEnum` and nothing else; "does not recur"
is ``recurrence_rule_id IS NULL`` on either template kind, which never reaches
a resolver (plan step R2e-3 retired the ``Once`` pattern that was the second
way to say it).
"""

import calendar as cal
import logging
from datetime import date
from decimal import InvalidOperation
from typing import NamedTuple

from app.extensions import db
from app.models.transaction import Transaction
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import RecurrenceConflict, ValidationError
from app.models.salary_profile import SalaryProfile
from app.services.recurrence import PlacementOutcome, rule_occurrences
from app.services._recurrence_common import (
    check_scenario_ownership,
    existing_rows_by_period,
    existing_rows_refusing_repeats,
    log_resource_access_denied,
    partition_regeneration_rows,
    regeneration_bound,
    query_rows_from_effective_date,
    report_schedule_gaps,
    should_skip_period,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RECURRENCE_GENERATED,
    EVT_RECURRENCE_REGENERATED,
    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED,
    log_event,
)

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
        gaps: Every occurrence date this rule names that the owner's schedule
            has NO pay period for -- a hole between two periods, not the
            ordinary tail past the last payday
            (:class:`~app.services.recurrence.PlacementOutcome`).  Reported by
            the write path (``_recurrence_common.report_schedule_gaps``) and
            ignored by the read-only predictor, so predicting never emits an
            operator alert.  Empty on every schedule the writer has produced
            (production: 61 contiguous periods, measured 2026-08-08); plan
            ledger row **D7**, cause F-10.
        projected_id: The ``Projected`` status id every generated row carries.
    """

    rule: RecurrenceRule
    placements: tuple[PlannedOccurrence, ...]
    gaps: tuple[date, ...]
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

    # The occurrence walk answers in SchedulePeriod values; row creation needs
    # the ORM rows.  ``write_periods`` is keyed on ``pay_periods.id``, so the
    # single lookup below does BOTH jobs -- narrow to the window, and hand back
    # the row to write into.  Dropping that intersection would make a schedule
    # extend re-walk every historical period and cost O(schedule) writes
    # instead of O(new).
    window = schedule.write_periods
    placements = []
    gaps = []
    for placement in rule_occurrences(rule, schedule.calendar):
        if placement.outcome is PlacementOutcome.SCHEDULE_GAP:
            # Owed, and no paycheck covers the day.  Collected rather than
            # logged here: the WRITE path reports it (see GenerationPlan.gaps),
            # so the read-only predictor stays silent.
            gaps.append(placement.occurrence)
        if placement.period is None:
            continue
        if (
            effective_from is not None
            and placement.period.end_date < effective_from
        ):
            continue
        period = window.get(placement.period.period_id)
        if period is not None:
            placements.append(PlannedOccurrence(placement.occurrence, period))
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    return GenerationPlan(rule, tuple(placements), tuple(gaps), projected_id)


def generate_for_template(template, schedule, scenario_id, effective_from=None):
    """Generate transactions for a template across a pay-period window.

    This is the main entry point.  It:
      1. Determines which occurrences the rule names and which pay period each
         lands in, resolved against the OWNER's whole schedule and narrowed to
         the pass's window.
      2. Skips periods that already have an overridden, deleted, or immutable entry.
      3. Creates new auto-generated transactions for applicable periods.

    Args:
        template:       A TransactionTemplate with a loaded recurrence_rule.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The scenario to generate into.
        effective_from: Optional date -- only generate for periods ending on or
                        after this date.  ``None`` applies no lower bound.

    Returns:
        List of newly created Transaction objects.
    """
    # Resolve the shared gating + occurrence-matching preamble: cross-user
    # defense, rule-present gating, and the occurrence walk narrowed to the
    # window.  A None result means generate nothing (ownership failed, or no
    # rule).  See resolve_generation_plan.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence generation",
    )
    if plan is None:
        return []

    # A hole in the owner's schedule is named here rather than inside
    # ``resolve_generation_plan``: the read-only predictor shares that call and
    # runs once per envelope row on the carry-forward path, so reporting there
    # would emit N operator alerts for one prediction.  See
    # _recurrence_common.report_schedule_gaps.
    report_schedule_gaps(logger, template, scenario_id, plan.gaps)

    # What is already there (to avoid duplicates and respect overrides), and
    # the refusal of a paycheck this pass would write into TWICE -- the unique
    # index holds one row per (template, period, scenario), and forward
    # generation legitimately names a paycheck more than once at a cadence of
    # 30 days or more.  One call because the order matters; see
    # _recurrence_common.existing_rows_refusing_repeats.
    existing = existing_rows_refusing_repeats(
        Transaction, Transaction.template_id,
        template, scenario_id, plan.placements,
    )

    # Check if this template has a linked salary profile for paycheck calculation.
    salary_profile = _get_salary_profile(template.id)

    created = []
    for period in (row.period for row in plan.placements):
        existing_txns = existing.get(period.id, [])

        # Skip periods that already hold a template-linked row (immutable,
        # override, soft-deleted, or simply already auto-generated).
        if should_skip_period(existing_txns):
            continue

        # Determine the amount -- use paycheck calculator if salary-linked.
        # The OWNER's whole schedule, never the window: see
        # _get_transaction_amount for the $502.45 that distinction was worth.
        amount = _get_transaction_amount(
            template, salary_profile, period, schedule.periods,
        )

        # Compute the due date from the rule and period context.
        due = compute_due_date(plan.rule, period)

        # No existing entry -- create a new one.
        txn = Transaction(
            account_id=template.account_id,
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            status_id=plan.projected_id,
            name=template.name,
            category_id=template.category_id,
            transaction_type_id=template.transaction_type_id,
            estimated_amount=amount,
            is_override=False,
            is_deleted=False,
            due_date=due,
        )
        db.session.add(txn)
        created.append(txn)

    db.session.flush()
    log_event(logger, logging.INFO, EVT_RECURRENCE_GENERATED, BUSINESS,
              "Transactions generated from template",
              user_id=template.user_id,
              template_id=template.id,
              scenario_id=scenario_id,
              count=len(created))
    return created


def can_generate_in_period(template, period, scenario_id, *, schedule):
    """Return True iff ``generate_for_template`` would create a row in *period*.

    Read-only mirror of ``generate_for_template``'s gating logic.
    Useful to callers that need to predict the engine's behaviour
    without mutating -- e.g. the carry-forward preview endpoint
    (``carry_forward_service.preview_carry_forward``) shows the user
    whether a missing target canonical would be auto-generated or
    whether the carry-forward will refuse.

    The decision applies the same gates as ``generate_for_template``:

      1. Cross-user defense: scenario must belong to the template's
         user.
      2. Template must have a recurrence rule.
      3. The period must be one the rule fires in.
      4. The (template, period, scenario) tuple must have NO existing
         rows -- not even soft-deleted ones.  The engine's per-row
         skip logic treats any existing row as a "do not generate"
         signal, so a soft-deleted carry-over also blocks generation.

    **Steps 1-3 are now literally ``resolve_generation_plan``, and until plan
    step R4b they were a hand-written MIRROR of it that disagreed.**  The
    mirror passed ``[period]`` as both the schedule and the window, so the
    rule was resolved against a ONE-period schedule: the anchor was measured
    from that period's own start rather than the owner's schedule opening, a
    rule's chosen start period could not be found at all, and ``Monthly
    First`` -- whose anchor asks which month a payday falls in -- saw one
    month with one payday and answered "yes" for every period.  Measured on a
    clone of production, 2026-08-08: for the live ``Phone Allowance`` rule the
    mirror said the engine would generate in **32 of 61 periods** where the
    real answer is each month's FIRST paycheck only.  Because the carry-forward
    executor acts on this prediction and then calls generation with the same
    one-period window, the two agreed with each other and produced a spurious
    row.  Sharing the call removes the second opinion rather than correcting
    it.

    The only step still written out here is 4: ``generate_for_template``
    applies it per period as it walks, and this function needs it for the one
    period it was asked about.

    **The schedule is threaded in rather than built here**, and that is a cost
    decision the carry-forward path forces.  This predicate runs ONCE PER
    ENVELOPE ROW being rolled forward (``_classify_leftover_target``), and
    building a schedule per call would issue one ``get_all_periods`` query and
    one full forward occurrence walk per row -- the redundant-producer shape
    ``period_population`` documents avoiding three modules away.  The caller
    resolves one schedule for the request and passes it down.

    Args:
        template: The TransactionTemplate to check.  Must have its
            ``recurrence_rule`` relationship loaded (the same
            assumption ``generate_for_template`` makes).
        period: The PayPeriod object the canonical would land in.  Its
            membership in the engine's answer is the question; it does NOT
            have to be *schedule*'s write window.
        scenario_id: The scenario that would receive the canonical.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule`.

    Returns:
        bool -- True when the engine would create a row, False when
        any of the gating conditions would skip it.
    """
    plan = resolve_generation_plan(
        template, schedule, scenario_id, None,
        block_message="Blocked cross-user recurrence generation prediction",
    )
    if plan is None:
        return False
    # Membership rather than emptiness: *schedule*'s window may be wider than
    # the one period asked about (the carry-forward context narrows it to the
    # target, but a caller threading a whole-schedule value is equally valid),
    # so the answer is "does the engine name THIS period".
    if period.id not in {row.period.id for row in plan.placements}:
        return False

    # Engine refuses to overwrite ANY existing row -- skip if even one
    # row (including soft-deleted) sits in (template, period, scenario).
    existing = _get_existing_map(template.id, scenario_id, [period.id])
    if existing.get(period.id):
        return False

    return True


def regenerate_for_template(template, schedule, scenario_id, effective_from=None):
    """Delete non-overridden auto-generated entries and regenerate.

    Used when a template's amount or recurrence rule changes.  Implements
    the state machine rules from §4.8:
      1. Delete all auto_generated (non-overridden, non-deleted) transactions
         on or after the effective date.
      2. Regenerate from the rule.
      3. Return conflicts (overridden and deleted entries) for the caller
         to present to the user.

    **The delete sweep and the regeneration share ONE bound, and that is
    load-bearing.**  ``effective_from`` bounds an SQL sweep over
    ``pay_periods.end_date``, so "no lower bound" has to become a date before
    it can be compared against a column -- and the date it becomes must be the
    opening of the WINDOW this pass writes into, not of the whole schedule.
    Taking the schedule's opening instead would DELETE every non-override row
    from the owner's first payday forward while regenerating only inside the
    window, destroying rows nothing would recreate.  No route reaches that
    today -- both callers pass a whole-schedule window, where the two bounds
    coincide -- which is exactly why the asymmetry is closed here rather than
    left for someone to discover with a narrow window.

    Args:
        template:       The updated TransactionTemplate.
        schedule:       The owner's
                        :class:`~app.services.generation_schedule.GenerationSchedule`.
        scenario_id:    The target scenario.
        effective_from: Date from which to regenerate (default: the WRITE
                        WINDOW's first payday -- see above; the sweep and the
                        regeneration must not use different bounds).

    Returns:
        List of newly created Transaction objects.

    Raises:
        RecurrenceConflict: If overridden or deleted entries exist that need
                            user confirmation.  The caller should catch this,
                            present the options, and call resolve_conflicts().
    """
    # Defense-in-depth: verify ownership before deleting and regenerating.
    if not check_scenario_ownership(
        logger, template, scenario_id,
        block_message="Blocked cross-user recurrence regeneration",
    ):
        return []

    effective_from = regeneration_bound(schedule, effective_from)

    # Find all existing template-linked transactions on or after effective_from,
    # then partition them into conflicts vs rows safe to delete and regenerate.
    existing = query_rows_from_effective_date(
        Transaction, Transaction.template_id,
        template.id, scenario_id, effective_from,
    )
    overridden_ids, deleted_ids, to_delete = partition_regeneration_rows(existing)

    # Delete the safe-to-remove entries.
    for txn in to_delete:
        db.session.delete(txn)
    db.session.flush()

    # Regenerate new entries.
    created = generate_for_template(
        template, schedule, scenario_id, effective_from,
    )

    log_event(
        logger, logging.INFO, EVT_RECURRENCE_REGENERATED, BUSINESS,
        "Recurrence regenerated for template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        deleted_count=len(to_delete),
        created_count=len(created),
        overridden_conflict_count=len(overridden_ids),
        deleted_conflict_count=len(deleted_ids),
    )

    # If there are conflicts, raise so the caller can prompt the user.
    if overridden_ids or deleted_ids:
        raise RecurrenceConflict(overridden=overridden_ids, deleted=deleted_ids)

    return created


def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.
    Each transaction is ownership-checked via its pay_period.user_id before
    any modification -- transactions not owned by ``user_id`` are silently
    skipped (defense-in-depth against IDOR).

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' -- clear override/delete, apply new amount.
                         'keep' -- leave the transaction unchanged.
        user_id:         The requesting user's ID.  Transactions not owned
                         by this user are skipped.
        new_amount:      The new default amount (required if action='update').
    """
    if action == "keep":
        # Nothing to do -- the user wants to keep their overrides.
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts kept (no mutation)",
            user_id=user_id, action=action,
            transaction_id_count=len(transaction_ids),
        )
        return

    if action == "update":
        resolved_count = 0
        skipped_count = 0
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                skipped_count += 1
                continue

            # Ownership check: Transaction -> PayPeriod -> user_id.
            if txn.pay_period.user_id != user_id:
                # Cross-user request: emit the IDOR-detection event so
                # SOC tooling sees the probe.  ACCESS-category is the
                # right home for this -- the requester does not own
                # the row even though we silently skip it.
                log_resource_access_denied(
                    logger,
                    user_id=user_id,
                    model="Transaction",
                    pk=txn_id,
                    owner_id=txn.pay_period.user_id,
                )
                skipped_count += 1
                continue

            # Transfer shadow guard (CLAUDE.md Transfer invariant 4 / F-007).
            # Shadow rows (transfer_id IS NOT NULL) are owned by the transfer
            # service.  resolve_conflicts is reachable only from the
            # transaction-template regeneration flow, which never produces
            # shadow IDs in its conflict set; a shadow ID arriving here is
            # therefore an internal logic error or an attacker probe.
            # Mutating a shadow directly would desynchronise the parent
            # transfer's amount/status/period from its sibling shadow and
            # silently corrupt the user's balance projections.  Refuse.
            if txn.transfer_id is not None:
                log_event(
                    logger, logging.WARNING,
                    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED, BUSINESS,
                    "Refused to mutate transfer shadow via resolve_conflicts",
                    user_id=user_id,
                    transaction_id=txn_id,
                    transfer_id=txn.transfer_id,
                    action=action,
                )
                raise ValidationError(
                    "Cannot modify transfer shadow transactions via "
                    "resolve_conflicts.  Route transfer mutations through "
                    "transfer_service."
                )

            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                txn.estimated_amount = new_amount
            resolved_count += 1
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts resolved (update)",
            user_id=user_id, action=action,
            resolved_count=resolved_count,
            skipped_count=skipped_count,
            new_amount=str(new_amount) if new_amount is not None else None,
        )


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


def _get_existing_map(template_id, scenario_id, period_ids):
    """Group this template's existing transactions in *period_ids* by period.

    A one-line binding of :func:`_recurrence_common.existing_rows_by_period` to
    this engine's model, so the two engines' generate paths run the identical
    query.  Plan step R4b-2 hoisted the body: the transfer engine carried a
    byte-similar copy, which is the duplication that module exists to hold.
    """
    return existing_rows_by_period(
        Transaction, Transaction.template_id,
        template_id, scenario_id, period_ids,
    )


def is_salary_linked_template(template_id):
    """Return True iff an active salary profile drives this template's amounts.

    A salary-linked template's instance amounts are paycheck-calculated per
    period (:func:`_get_transaction_amount`), so its ``default_amount`` is
    vestigial: editing it does not change generated rows.  The update route
    uses this to skip the amount-change conflict chooser for such templates
    (their ``default_amount`` diff is not a real per-instance amount change).
    """
    return _get_salary_profile(template_id) is not None


def _get_salary_profile(template_id):
    """Check if a template has a linked salary profile.

    Returns the SalaryProfile if found, None otherwise.
    """
    return (
        db.session.query(SalaryProfile)
        .filter_by(template_id=template_id, is_active=True)
        .first()
    )


def _get_transaction_amount(template, salary_profile, period, all_periods):
    """Determine the transaction amount, using paycheck calculator if salary-linked.

    Resolves tax configs for the period's OWN tax year via the shared
    ``load_tax_configs_for_year`` SSOT, which substitutes the latest
    CONFIGURED year at or before it when that year has none.  The salary
    projection page and the
    live net-pay recompute (``income_service.live_projected_net``) resolve
    the SAME way (DH-#30), so the grid's stored income amount and the
    salary page's live-calculated net pay agree on which year's brackets
    and FICA wage base/cap apply -- they cannot silently diverge.

    **``all_periods`` must be the OWNER's WHOLE schedule, and passing the
    caller's window instead was a live money defect** (plan ledger row
    **D25**, closed at plan step R4b-1).  ``calculate_paycheck`` reads this
    argument for FIVE separate judgements, every one of which needs periods
    the pass itself is not writing into: the annual rounding reconciliation
    (``_gross_biweekly_for_period``), THIRD-PAYCHECK detection
    (``_is_third_paycheck``), the first-paycheck-of-month deductions
    (``_is_first_paycheck_of_month``), the FICA wage-base cumulative
    (``_get_cumulative_wages``), and a deduction's ANNUAL CAP
    (``_cumulative_deduction_before``, whose own docstring names the identical
    hazard -- a partial context under-counts the cumulative and defers the cap,
    so the deduction keeps being charged after it should have stopped).  The
    fifth was missing from an earlier draft of this paragraph and an
    adversarial review added it.  ``period_population`` hands the engines only
    the NEWLY created periods, so a schedule extend used to answer all four
    from a 1-3 period sample.

    Measured 2026-08-08 on a streamed clone of production: transaction 2756,
    pay period 2028-06-29 -- the THIRD paycheck of June 2028 -- was generated
    by an extend at **$2,814.45** where the whole schedule gives
    **$3,316.90**.  The extend could not see the other two June paychecks, so
    it did not know this was a third one and applied the deductions a third
    paycheck skips: the stored amount is **$502.45 low**.  Every future extend
    landing on a third paycheck would have written another.

    **What the stale amount did and did not reach, measured rather than
    reasoned.**  The balance projection and the grid CELL both recompute
    projected salary income at read time
    (``income_service.live_projected_net``, threaded through
    ``cash_ledger.live_amount_overrides``), so neither ever showed the stale
    figure: on an unmigrated clone the live recompute answers $3,316.90 for
    that row, and a period-by-period balance diff over both accounts and all
    61 periods moves by exactly the three deleted ``Phone Allowance`` income
    rows and by nothing else.  What the stale column DOES reach is the grid's
    inline amount editor, which pre-fills from
    ``Transaction.estimated_amount`` -- and saving that form sets
    ``is_override = True`` (``routes/transactions/mutations.py``), the very
    flag that EXCLUDES a row from the live recompute.  So the wrong figure was
    one click away from becoming the projection, permanently.
    """
    if salary_profile is None:
        return template.default_amount

    try:
        # Local imports: the tax-config / paycheck fallback tests patch the
        # SOURCE modules (app.services.tax_config_service.load_tax_configs and
        # app.services.paycheck_calculator.calculate_paycheck -- the
        # testing-standards-preferred patch target).  A module-level
        # ``from ... import`` would bind the name once at import and not see
        # the patch, so these imports stay local.
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patches of app.services.paycheck_calculator take effect.
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patch of
        # app.services.tax_config_service.load_tax_configs_for_year takes
        # effect; a module-level import would bind the name before the patch.
        from app.services.tax_config_service import load_tax_configs_for_year  # pylint: disable=import-outside-toplevel

        # Resolve the period's own tax year, substituting the latest
        # CONFIGURED year at or before it when that year has none (else
        # future-year periods would produce zero withholding and the grid
        # would disagree with the salary page).  The rule is owned ONCE by
        # load_tax_configs_for_year, the SSOT shared with the salary
        # projection and the year-end summary (DH-#30).
        tax_configs = load_tax_configs_for_year(
            salary_profile.user_id, salary_profile, period.start_date.year,
        )

        # Load calibration override if the profile has one.
        calibration = getattr(salary_profile, "calibration", None)

        breakdown = paycheck_calculator.calculate_paycheck(
            salary_profile, period, all_periods, tax_configs,
            calibration=calibration,
        )
        return breakdown.earnings.net_pay

    except (InvalidOperation, ZeroDivisionError, TypeError, KeyError) as exc:
        logger.error(
            "Paycheck calculation failed for salary profile %d in "
            "period %s: %s. Using template default_amount.",
            salary_profile.id,
            period.start_date,
            exc,
        )
        return template.default_amount
