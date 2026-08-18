"""
Shekel Budget App -- Recurrence Engine: filling periods that hold no row

:func:`generate_for_template`, the entry point that CREATES, and
:func:`can_generate_in_period`, its read-only mirror for callers that need to
predict it without mutating.

**It never touches a row that already exists** -- any existing row, in any
state, makes its paycheck skipped -- which is the difference between this leaf
and ``_maintain``: generating fills gaps, maintaining brings existing rows back
into line.  Both take their derived columns from the same
:class:`~app.services.recurrence_engine._amounts.DerivedRowFields`, so a new
one is written on a created row and kept current on a maintained one from the
same edit.
"""
import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.services._recurrence_common import (
    existing_rows_by_period,
    existing_rows_refusing_repeats,
    should_skip_period,
)
from app.services.recurrence_engine._amounts import (
    _derive_row_fields,
    _get_salary_profile,
)
from app.services.recurrence_engine._plan import resolve_generation_plan
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_GENERATED,
    log_event,
)

logger = logging.getLogger(__name__)



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
    salary_profile = _get_salary_profile(template)

    created = []
    for period in (row.period for row in plan.placements):
        existing_txns = existing.get(period.id, [])

        # Skip periods that already hold a template-linked row (immutable,
        # override, soft-deleted, or simply already auto-generated).
        if should_skip_period(existing_txns):
            continue

        # No existing row -- create one, taking every derived column from the
        # ONE statement of them (:class:`DerivedRowFields`), which
        # ``regenerate_for_template`` assigns onto an existing row from the
        # same definition.  The three columns below that are NOT in it say what
        # this row IS rather than what the template says: it is the rule's own
        # row, live, and not yet an actual event.
        txn = Transaction(
            **_derive_row_fields(
                template, plan.rule, salary_profile, period, schedule,
            )._asdict(),
            template_id=template.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            status_id=plan.projected_id,
            is_override=False,
            is_deleted=False,
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
    building a schedule per call would issue that schedule's THREE queries --
    ``get_all_periods``, and the payday and cadence reads
    ``pay_calendar.calendar_for`` makes (plan step C2-b2 moved the calendar onto
    that one door) -- plus one full forward occurrence walk, per row.  That is
    the redundant-producer shape ``period_population`` documents avoiding three
    modules away.  The caller
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
