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
made :func:`match_periods` a thin adapter over the forward occurrence engine
(:mod:`app.services.recurrence`), which walks the rule's own cadence and then
places each occurrence on a pay period.  What survives here is the GENERATION
half: gating, the per-period skip predicate, amount resolution, row creation,
and the regenerate / conflict state machine.

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
from collections import defaultdict
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
from app.services.recurrence import (
    occurrence_placements,
    recurrence_spec,
    resolve,
)
from app.services._recurrence_common import (
    check_scenario_ownership,
    log_resource_access_denied,
    partition_regeneration_rows,
    regeneration_bound,
    query_rows_from_effective_date,
    refuse_unstorable_repeats,
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


class GenerationPlan(NamedTuple):
    """Resolved inputs a recurrence generate pass needs after gating.

    Returned by :func:`resolve_generation_plan` once the cross-user
    ownership check and the rule-present gating have passed, so the caller
    can proceed straight to model-specific row creation.  Public (no
    leading underscore) because it is the return contract of the public
    :func:`resolve_generation_plan`, which the transfer engine consumes.
    """

    rule: RecurrenceRule
    matching_periods: list[PayPeriod]
    projected_id: int


def resolve_generation_plan(
    template, schedule, scenario_id, effective_from, *, block_message,
):
    """Run the shared gating + period-matching preamble for a generate pass.

    Both this module's ``generate_for_template`` and the transfer
    engine's identical preamble (``app/services/transfer_recurrence.py``)
    perform the same steps before their model-specific row creation: the
    cross-user ownership check, the rule-present gating, and the pattern
    match against the owner's schedule.  Centralising them guarantees the two
    engines cannot drift on which periods a rule applies to.  Public (no
    leading underscore) because the transfer engine calls it cross-module --
    the shared preamble is deliberately part of this module's public surface,
    like :func:`match_periods`.

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
    ``tests/oracles/recurrence_baseline.txt`` over 428 shapes.  ``None`` now
    plainly means "no lower window bound".

    Args:
        template: The (Transaction|Transfer)Template to generate from.
        schedule: The owner's
            :class:`~app.services.generation_schedule.GenerationSchedule` --
            their whole pay-period schedule plus the window this pass may
            write into.
        scenario_id: The scenario to generate into.
        effective_from: Optional lower bound on the window; periods ENDING
            before it are dropped.  ``None`` applies no bound.
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

    # Placement answers in SchedulePeriod values; row creation needs the ORM
    # rows.  ``write_periods`` is keyed on ``pay_periods.id``, so this single
    # membership test does BOTH jobs -- narrow to the window, and hand back
    # the row to write into.
    window = schedule.write_periods
    matching_periods = [
        window[period.period_id]
        for period in match_periods(rule, schedule.calendar, effective_from)
        if period.period_id in window
    ]
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    return GenerationPlan(rule, matching_periods, projected_id)


def generate_for_template(template, schedule, scenario_id, effective_from=None):
    """Generate transactions for a template across a pay-period window.

    This is the main entry point.  It:
      1. Determines which periods the rule applies to, resolved against the
         OWNER's whole schedule and narrowed to the pass's window.
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
    # Resolve the shared gating + period-matching preamble: cross-user
    # defense, rule-present gating, and the pattern match narrowed to the
    # window.  A None result means generate nothing (ownership failed, or no
    # rule).  See resolve_generation_plan.
    plan = resolve_generation_plan(
        template, schedule, scenario_id, effective_from,
        block_message="Blocked cross-user recurrence generation",
    )
    if plan is None:
        return []

    # Check for existing transactions to avoid duplicates and respect overrides.
    existing = _get_existing_map(template.id, scenario_id, plan.matching_periods)

    # Refuse a paycheck this pass would write into TWICE before writing
    # anything: the unique index holds one row per (template, period,
    # scenario), and forward generation legitimately names a paycheck more
    # than once at a cadence of 30 days or more.  See refuse_unstorable_repeats.
    refuse_unstorable_repeats(template, plan.matching_periods, existing)

    # Check if this template has a linked salary profile for paycheck calculation.
    salary_profile = _get_salary_profile(template.id)

    created = []
    for period in plan.matching_periods:
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
    if period.id not in {row.id for row in plan.matching_periods}:
        return False

    # Engine refuses to overwrite ANY existing row -- skip if even one
    # row (including soft-deleted) sits in (template, period, scenario).
    existing = _get_existing_map(template.id, scenario_id, [period])
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


# --- Pattern Matching ----------------------------------------------------


def match_periods(rule, calendar, effective_from):
    """Return the periods this recurrence fires in, in occurrence order.

    Public: the recurrence engine generates against it, the transfer engine
    reaches it through :func:`resolve_generation_plan`, and the templates
    preview route and the Recurring surface render against it -- so it is
    deliberately part of this module's public surface rather than a
    leading-underscore helper.

    **Plan step R4a turned this into an ADAPTER.**  It used to be a REVERSE
    mapping: five near-identical ``_match_*`` helpers scanned every candidate
    period and asked whether that period contained the rule's target day, each
    of them inspecting only the months of a period's two ENDPOINTS.  A period
    spanning more than two months could therefore not match the interior ones,
    and ``cadence_days`` is user-selectable 1..365
    (``schemas/validation/pay_periods.py``), so half a rule's occurrences
    vanished at a reachable configuration (plan defect **D3**).  Generation is
    now stated FORWARD -- walk the rule's own cadence from its first
    occurrence, then place each occurrence on a pay period -- which removes the
    defect structurally rather than by widening the scan.  See
    :mod:`app.services.recurrence._occurrence`.

    **The rule's own window is no longer applied here, and the difference is
    plan defect D5.**  ``start_date`` and ``end_date`` used to filter the
    candidate PERIODS -- ``end_date`` against a period's START -- so a row was
    generated whose OWN occurrence date lay outside the window the user set (a
    monthly-15th rule ending 2025-06-05 generated a row due 2025-06-15).  Both
    bounds now bind the OCCURRENCE instead: ``start_date`` through the anchor
    :func:`app.services.recurrence.resolve` derives, ``end_date`` through the
    engine's own stopping bound (ruling R-R6).  They stay unbypassable by a
    caller's ``effective_from``, which is what lets a loan payment's
    ``start_date`` guarantee no installment is generated before the loan
    originates (plan step C9a).

    **A period can appear TWICE, and that is the honest answer.**  At a cadence
    of 30 days or more a monthly bill legitimately occurs several times inside
    one paycheck; the reverse matcher walked PAYCHECKS and so silently emitted
    one row for three months of rent.  ``budget.transactions`` cannot yet HOLD
    the separate rows -- ``idx_transactions_template_period_scenario`` is
    unique over ``(template, period, scenario)`` -- so generation REFUSES,
    naming the definition and the paycheck
    (``_recurrence_common.refuse_unstorable_repeats``), and plan step R5
    re-keys the index onto the occurrence and lifts the refusal.  This
    function reports what the cadence names; it does not decide what is
    storable, which is why the refusal lives on the write path and the
    read-only surfaces (the preview, the Recurring page) still render the
    repeats.

    **An occurrence the schedule cannot host is dropped here**, which is
    exactly what the old matcher did (it never looked), and is plan ledger row
    **D7**: ``app.services.recurrence.occurrence_placements`` reports such an
    occurrence rather than dropping it, and plan step R4b-2 is where generation
    starts reading the report.

    **The schedule is now an argument in its own right** (plan step R4b-1).
    This used to take the caller's candidate PERIODS and build the calendar
    from them, so a caller passing a subset resolved the rule against that
    subset -- plan ledger rows **D22**, **D25** and **D2**, all measured live.
    A window is now applied by the generation seam AFTER the answer, never
    before it; see :func:`resolve_generation_plan`.

    Args:
        rule:           The RecurrenceRule object, which names the pattern.
        calendar:       The OWNER's whole pay-period schedule
            (:class:`~app.services.recurrence.PeriodCalendar`), which the
            rule's first occurrence is measured against.
        effective_from: Drop periods that END before this date.  The same
            predicate the old matcher applied to its candidate list.  ``None``
            applies no bound.

    Returns:
        List of :class:`~app.services.recurrence.SchedulePeriod` values,
        ascending by occurrence date, with a period repeated once per
        occurrence it hosts.  The resolver's own value type rather than ORM
        rows: this function is a pure question about a schedule, and the one
        caller that must WRITE a row (:func:`resolve_generation_plan`) already
        holds the owner's rows to map ``period_id`` back onto.

    Raises:
        RecurrenceResolutionError: When the rule cannot be resolved against
            *calendar* -- an unmodelled pattern, a non-positive interval, or a
            day / month outside its column's domain.  The old matcher answered
            ``[]`` for the first of those and ``ValueError`` for the last; both
            now name the offending value.
        RecurrenceGenerationError: When the resolved value names something the
            occurrence engine cannot walk -- a business-day shift (plan step
            R8 is its first author) or a placement with no rule.  Unreachable
            from any value ``resolve`` can produce today, and listed so a
            later step that makes it reachable finds the contract stated.
    """
    # An empty schedule has no period to match and no anchor to measure --
    # ``resolve`` refuses it, because a first occurrence is measured against a
    # schedule.  Answering [] is the total answer to "which of these none
    # periods match", not a guard against a bad state, and it is what keeps a
    # brand-new owner's Recurring surface rendering instead of raising.
    if not calendar.periods:
        return []

    resolved = resolve(recurrence_spec(rule), calendar)
    return [
        placement.period
        for placement in occurrence_placements(resolved, calendar)
        if placement.period is not None
        and (
            effective_from is None
            or placement.period.end_date >= effective_from
        )
    ]


def compute_due_date(rule, period):
    """Compute the due_date for a generated transaction.

    Derives the calendar date the bill is actually due, using the
    recurrence rule's scheduling day and optional due-day override.
    Public (no leading underscore): the transfer engine, the transfers
    preview route, the due-date backfill script, and a data migration all
    derive a row's due date through this same pure helper, so it is
    deliberately part of this module's public surface (like
    :func:`match_periods`) rather than a leading-underscore internal.

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


def _get_existing_map(template_id, scenario_id, periods):
    """Build a dict of period_id → [Transaction, ...] for existing template entries.

    Uses a list per period to avoid silent dict overwrites when a deleted and
    non-deleted transaction share the same period_id.  Fetches all entries
    (including deleted) to check for duplicates and respect override/delete flags.
    """
    period_ids = [p.id for p in periods]
    if not period_ids:
        return {}

    existing = (
        db.session.query(Transaction)
        .filter(
            Transaction.template_id == template_id,
            Transaction.scenario_id == scenario_id,
            Transaction.pay_period_id.in_(period_ids),
        )
        .all()
    )
    result = defaultdict(list)
    for txn in existing:
        result[txn.pay_period_id].append(txn)
    return result


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
    ``load_tax_configs_for_year`` SSOT (current-year fallback when a future
    year has no configs at all).  The salary projection page and the
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
        # tests' patches of app.services.tax_config_service.load_tax_configs
        # take effect (load_tax_configs_for_year calls it internally).
        from app.services.tax_config_service import load_tax_configs_for_year  # pylint: disable=import-outside-toplevel

        # Resolve the period's own tax year, falling back to the current
        # year when that year has no configs at all (else future-year
        # periods would produce zero federal tax and the grid would
        # disagree with the salary page).  The fallback rule is owned ONCE
        # by load_tax_configs_for_year, the SSOT shared with the salary
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
