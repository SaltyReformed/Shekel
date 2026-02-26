"""
Shekel Budget App — Transfer Recurrence Engine

Parallel to recurrence_engine.py but generates Transfer records instead
of Transaction records.  Reuses _match_periods from the transaction
recurrence engine for pattern matching.

Key differences from transaction recurrence:
  - No salary linkage or category.
  - Single amount column (no estimated/actual split).
  - Simpler amount logic: always uses template.default_amount.
"""

import logging
from collections import defaultdict

from app.extensions import db
from app.models.transfer import Transfer
from app.models.pay_period import PayPeriod
from app.models.ref import Status
from app.services.recurrence_engine import _match_periods
from app.exceptions import RecurrenceConflict

logger = logging.getLogger(__name__)

# Statuses that are historical — never modified by the recurrence engine.
IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})


def generate_for_template(template, periods, scenario_id, effective_from=None):
    """Generate transfers for a template across the given pay periods.

    Args:
        template:       A TransferTemplate with a loaded recurrence_rule.
        periods:        List of PayPeriod objects to consider (ordered by index).
        scenario_id:    The scenario to generate into.
        effective_from: Optional date — only generate for periods starting on or
                        after this date.

    Returns:
        List of newly created Transfer objects.
    """
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

    projected_status = db.session.query(Status).filter_by(name="projected").one()

    matching_periods = _match_periods(rule, pattern_name, periods, effective_from)
    existing = _get_existing_map(template.id, scenario_id, matching_periods)

    created = []
    for period in matching_periods:
        existing_xfers = existing.get(period.id, [])

        should_skip = False
        for xfer in existing_xfers:
            status_name = xfer.status.name if xfer.status else "projected"
            if status_name in IMMUTABLE_STATUSES:
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

        xfer = Transfer(
            user_id=template.user_id,
            transfer_template_id=template.id,
            from_account_id=template.from_account_id,
            to_account_id=template.to_account_id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            status_id=projected_status.id,
            name=template.name,
            amount=template.default_amount,
            is_override=False,
            is_deleted=False,
        )
        db.session.add(xfer)
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
    if effective_from is None and periods:
        effective_from = periods[0].start_date

    existing = (
        db.session.query(Transfer)
        .join(PayPeriod, Transfer.pay_period_id == PayPeriod.id)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.scenario_id == scenario_id,
            PayPeriod.start_date >= effective_from,
        )
        .all()
    )

    overridden_ids = []
    deleted_ids = []
    to_delete = []

    for xfer in existing:
        status_name = xfer.status.name if xfer.status else "projected"

        if status_name in IMMUTABLE_STATUSES:
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


def resolve_conflicts(transfer_ids, action, new_amount=None):
    """Resolve override/delete conflicts after a regeneration.

    Args:
        transfer_ids: List of Transfer IDs to resolve.
        action:       'update' — clear override/delete, apply new amount.
                      'keep' — leave the transfer unchanged.
        new_amount:   The new default amount (required if action='update').
    """
    if action == "keep":
        return

    if action == "update":
        for xfer_id in transfer_ids:
            xfer = db.session.get(Transfer, xfer_id)
            if xfer is None:
                continue
            xfer.is_override = False
            xfer.is_deleted = False
            if new_amount is not None:
                xfer.amount = new_amount
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
