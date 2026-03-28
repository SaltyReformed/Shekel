"""
Shekel Budget App -- Transfer Recurrence Engine

Parallel to recurrence_engine.py but generates Transfer records instead
of Transaction records.  Reuses _match_periods from the transaction
recurrence engine for pattern matching.

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
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.models.pay_period import PayPeriod
from app.services.recurrence_engine import _match_periods
from app.services import transfer_service
from app.exceptions import RecurrenceConflict
from app import ref_cache
from app.enums import StatusEnum

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
    # Defense-in-depth: verify the template and scenario belong to the same
    # user.  The route layer already enforces this, but a mismatch here would
    # silently create transfers in another user's scenario (IDOR).
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != template.user_id:
        logger.warning(
            "Blocked cross-user transfer generation: template user_id=%s, "
            "scenario_id=%s", template.user_id, scenario_id,
        )
        return []

    rule = template.recurrence_rule
    if rule is None:
        return []

    pattern_name = rule.pattern.name
    if pattern_name == "once":
        return []

    if effective_from is None and rule.start_period_id and rule.start_period:
        effective_from = rule.start_period.start_date
    if effective_from is None and periods:
        effective_from = periods[0].start_date

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    matching_periods = _match_periods(rule, pattern_name, periods, effective_from)
    existing = _get_existing_map(template.id, scenario_id, matching_periods)

    created = []
    for period in matching_periods:
        existing_xfers = existing.get(period.id, [])

        should_skip = False
        for xfer in existing_xfers:
            if xfer.status and xfer.status.is_immutable:
                should_skip = True
                break
            if xfer.is_override:
                should_skip = True
                break
            if xfer.is_deleted:
                should_skip = True
                break
            # Already exists and unmodified.
            should_skip = True
            break

        if should_skip:
            continue

        # Delegate to the transfer service so shadow transactions are
        # created atomically alongside the transfer record.
        xfer = transfer_service.create_transfer(
            user_id=template.user_id,
            from_account_id=template.from_account_id,
            to_account_id=template.to_account_id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            amount=template.default_amount,
            status_id=projected_id,
            category_id=template.category_id,
            name=template.name,
            transfer_template_id=template.id,
        )
        created.append(xfer)

    db.session.flush()
    logger.info(
        "Generated %d transfers for template '%s' (id=%d)",
        len(created), template.name, template.id,
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
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != template.user_id:
        logger.warning(
            "Blocked cross-user transfer regeneration: template user_id=%s, "
            "scenario_id=%s", template.user_id, scenario_id,
        )
        return []

    if effective_from is None and periods:
        effective_from = periods[0].start_date

    existing = (
        db.session.query(Transfer)
        .join(PayPeriod, Transfer.pay_period_id == PayPeriod.id)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.scenario_id == scenario_id,
            PayPeriod.end_date >= effective_from,
        )
        .all()
    )

    overridden_ids = []
    deleted_ids = []
    to_delete = []

    for xfer in existing:
        if xfer.status and xfer.status.is_immutable:
            continue

        if xfer.is_override:
            overridden_ids.append(xfer.id)
            continue

        if xfer.is_deleted:
            deleted_ids.append(xfer.id)
            continue

        to_delete.append(xfer)

    for xfer in to_delete:
        db.session.delete(xfer)
    db.session.flush()

    created = generate_for_template(template, periods, scenario_id, effective_from)

    if overridden_ids or deleted_ids:
        raise RecurrenceConflict(overridden=overridden_ids, deleted=deleted_ids)

    return created


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
        return

    if action == "update":
        for xfer_id in transfer_ids:
            xfer = db.session.get(Transfer, xfer_id)
            if xfer is None:
                continue

            # Ownership check: Transfer has a direct user_id column.
            if xfer.user_id != user_id:
                logger.warning(
                    "resolve_conflicts blocked: transfer %d belongs to "
                    "user %d, not requesting user %d",
                    xfer_id, xfer.user_id, user_id,
                )
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

        db.session.flush()


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
