"""
Shekel Budget App -- Transfer Recurrence Engine

Parallel to recurrence_engine.py but generates Transfer records instead
of Transaction records.  The model-agnostic halves of the two engines
(the gating + pattern-matching preamble via
``recurrence_engine._resolve_generation_plan``, the per-period skip
predicate, the regenerate fetch/partition, and the cross-user audit
logging) are shared through that module and
``app/services/_recurrence_common.py`` so the two cannot drift.

Key differences from transaction recurrence:
  - No salary linkage.
  - Single amount column (no estimated/actual split).
  - Simpler amount logic: always uses template.default_amount.
  - Delegates transfer creation to transfer_service.create_transfer()
    so that shadow transactions are created atomically.
"""

import logging
from collections import defaultdict

from app.extensions import db
from app.models.transfer import Transfer
from app.services._recurrence_common import (
    check_scenario_ownership,
    log_resource_access_denied,
    partition_regeneration_rows,
    query_rows_from_effective_date,
    should_skip_period,
)
from app.services.recurrence_engine import _compute_due_date, _resolve_generation_plan
from app.services import transfer_service
from app.exceptions import RecurrenceConflict
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_TRANSFER_RECURRENCE_GENERATED,
    EVT_TRANSFER_RECURRENCE_REGENERATED,
    log_event,
)

logger = logging.getLogger(__name__)


def generate_for_template(template, periods, scenario_id, effective_from=None):
    """Generate transfers for a template across the given pay periods.

    Args:
        template:       A TransferTemplate with a loaded recurrence_rule.
        periods:        List of PayPeriod objects to consider (ordered by index).
        scenario_id:    The scenario to generate into.
        effective_from: Optional date -- only generate for periods starting on or
                        after this date.

    Returns:
        List of newly created Transfer objects.
    """
    # Resolve the shared gating + period-matching preamble (cross-user
    # defense, rule/ONCE gating, effective_from defaulting, pattern
    # match) via the transaction engine's helper -- the transfer engine
    # is a deliberate parallel and must apply the rule identically.  A
    # None result means generate nothing.  See
    # recurrence_engine._resolve_generation_plan.
    plan = _resolve_generation_plan(
        template, periods, scenario_id, effective_from,
        block_message="Blocked cross-user transfer recurrence generation",
    )
    if plan is None:
        return []

    existing = _get_existing_map(template.id, scenario_id, plan.matching_periods)

    created = []
    for period in plan.matching_periods:
        existing_xfers = existing.get(period.id, [])

        # Skip periods that already hold a template-linked transfer
        # (immutable, override, soft-deleted, or already auto-generated).
        if should_skip_period(existing_xfers):
            continue

        # Delegate to the transfer service so shadow transactions are
        # created atomically alongside the transfer record.  The due
        # date is computed from the recurrence rule via the same shared
        # helper the transaction engine uses (recurrence_engine.
        # _compute_due_date): a rule with a day_of_month (monthly,
        # quarterly, and -- via routes/loan/payment_transfer.py -- the
        # mortgage payment, whose rule carries day_of_month=payment_day) yields
        # that calendar day placed in the period's month, so the
        # calendar/dashboard match the loan card's true monthly due
        # date.  Rules without a day_of_month (every-paycheck, every-N)
        # fall back to period.start_date inside the helper, preserving
        # the payday-dated behaviour for those patterns.
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=template.user_id,
                from_account_id=template.from_account_id,
                to_account_id=template.to_account_id,
                pay_period_id=period.id,
                scenario_id=scenario_id,
                amount=template.default_amount,
                status_id=plan.projected_id,
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
                due_date=_compute_due_date(plan.rule, period),
            ),
        )
        created.append(xfer)

    db.session.flush()
    log_event(
        logger, logging.INFO, EVT_TRANSFER_RECURRENCE_GENERATED, BUSINESS,
        "Transfers generated from template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        count=len(created),
    )
    return created


def regenerate_for_template(template, periods, scenario_id, effective_from=None):
    """Delete non-overridden auto-generated transfers and regenerate.

    Args:
        template:       The updated TransferTemplate.
        periods:        List of PayPeriod objects.
        scenario_id:    The target scenario.
        effective_from: Date from which to regenerate (default: first period).

    Returns:
        List of newly created Transfer objects.

    Raises:
        RecurrenceConflict: If overridden or deleted entries exist.
    """
    # Defense-in-depth: verify ownership before deleting and regenerating.
    if not check_scenario_ownership(
        logger, template, scenario_id,
        block_message="Blocked cross-user transfer recurrence regeneration",
    ):
        return []

    if effective_from is None and periods:
        effective_from = periods[0].start_date

    # Find all existing template-linked transfers on or after effective_from,
    # then partition them into conflicts vs rows safe to delete and regenerate.
    existing = query_rows_from_effective_date(
        Transfer, Transfer.transfer_template_id,
        template.id, scenario_id, effective_from,
    )
    overridden_ids, deleted_ids, to_delete = partition_regeneration_rows(existing)

    # Route each deletion through the canonical hard-delete path
    # (Transfer Invariant 4): transfer_service.delete_transfer runs the
    # orphan-verification self-check and emits EVT_TRANSFER_HARD_DELETED
    # per deletion.  Shadow-pair atomicity is unchanged -- the underlying
    # ON DELETE CASCADE on transactions.transfer_id removes both shadows
    # either way -- but the forensic audit row and the orphan self-check
    # only exist on the service path.  See audit B6-03 / LOW-02.
    for xfer in to_delete:
        transfer_service.delete_transfer(xfer.id, template.user_id, soft=False)
    db.session.flush()

    created = generate_for_template(template, periods, scenario_id, effective_from)

    # Pylint: ``duplicate-code`` -- regenerate audit-log + conflict-raise
    # tail.  This is the parallel twin of
    # ``recurrence_engine.regenerate_for_template``: the model-agnostic
    # core (ownership check, partition, effective-date query) was already
    # hoisted into ``_recurrence_common`` (commit 7ed84c7); what remains is
    # the per-engine tail, which differs only in the audit event constant +
    # message.  Extracting it into a shared log helper was tried and
    # REVERTED (plan.md Phase 2 working note #3): one param per
    # ``log_event`` field trips ``too-many-arguments`` and -- because the
    # helper call site re-duplicates the identical kwargs -- dissolves no
    # cluster.  Documented one-sided ``duplicate-code`` disable instead;
    # the partner engine stays un-disabled.
    # pylint: disable=duplicate-code
    log_event(
        logger, logging.INFO, EVT_TRANSFER_RECURRENCE_REGENERATED, BUSINESS,
        "Transfer recurrence regenerated for template",
        user_id=template.user_id,
        template_id=template.id,
        scenario_id=scenario_id,
        deleted_count=len(to_delete),
        created_count=len(created),
        overridden_conflict_count=len(overridden_ids),
        deleted_conflict_count=len(deleted_ids),
    )

    if overridden_ids or deleted_ids:
        raise RecurrenceConflict(overridden=overridden_ids, deleted=deleted_ids)

    return created
    # pylint: enable=duplicate-code


def resolve_conflicts(transfer_ids, action, user_id, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Routes all mutations through the transfer service so shadow
    transactions are updated atomically.  Soft-deleted transfers are
    restored via ``transfer_service.restore_transfer`` before updating.

    Each transfer is ownership-checked via its direct ``user_id`` column
    before any modification -- transfers not owned by ``user_id`` are
    silently skipped (defense-in-depth against IDOR).

    Args:
        transfer_ids: List of Transfer IDs to resolve.
        action:       'update' -- clear override/delete, apply new amount.
                      'keep' -- leave the transfer unchanged.
        user_id:      The requesting user's ID.  Transfers not owned by
                      this user are skipped.
        new_amount:   The new default amount (required if action='update').
    """
    if action == "keep":
        log_event(
            logger, logging.INFO,
            EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Transfer recurrence conflicts kept (no mutation)",
            user_id=user_id, action=action,
            transfer_id_count=len(transfer_ids),
        )
        return

    if action == "update":
        resolved_count = 0
        skipped_count = 0
        for xfer_id in transfer_ids:
            xfer = db.session.get(Transfer, xfer_id)
            if xfer is None:
                skipped_count += 1
                continue

            # Ownership check: Transfer has a direct user_id column.
            if xfer.user_id != user_id:
                log_resource_access_denied(
                    logger,
                    user_id=user_id,
                    model="Transfer",
                    pk=xfer_id,
                    owner_id=xfer.user_id,
                )
                skipped_count += 1
                continue

            # Soft-deleted transfers must be restored before they can
            # be updated.  restore_transfer sets is_deleted=False on the
            # transfer and both shadows, and verifies invariants.
            if xfer.is_deleted:
                transfer_service.restore_transfer(xfer_id, user_id)

            # Build the update kwargs: clear override flag and apply
            # the new amount if provided.  update_transfer propagates
            # these to both shadow transactions atomically.
            svc_kwargs = {"is_override": False}
            if new_amount is not None:
                svc_kwargs["amount"] = new_amount

            transfer_service.update_transfer(xfer_id, user_id, **svc_kwargs)
            resolved_count += 1

        db.session.flush()
        log_event(
            logger, logging.INFO,
            EVT_TRANSFER_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Transfer recurrence conflicts resolved (update)",
            user_id=user_id, action=action,
            resolved_count=resolved_count,
            skipped_count=skipped_count,
            new_amount=str(new_amount) if new_amount is not None else None,
        )


def _get_existing_map(template_id, scenario_id, periods):
    """Build a dict of period_id → [Transfer, ...] for existing template entries."""
    period_ids = [p.id for p in periods]
    if not period_ids:
        return {}

    existing = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template_id,
            Transfer.scenario_id == scenario_id,
            Transfer.pay_period_id.in_(period_ids),
        )
        .all()
    )
    result = defaultdict(list)
    for xfer in existing:
        result[xfer.pay_period_id].append(xfer)
    return result
