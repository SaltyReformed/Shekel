"""
Shekel Budget App — Carry Forward Service

Moves all 'projected' transactions from a source pay period to a
target period (typically the current period).  This is the first-class
"Carry Forward Unpaid" operation described in §4.6.
"""

import logging

from app.extensions import db
from app.models.transaction import Transaction
from app.models.ref import Status
from app.exceptions import NotFoundError
from app.utils.log_events import log_event, BUSINESS

logger = logging.getLogger(__name__)


def carry_forward_unpaid(source_period_id, target_period_id):
    """Move all projected transactions from source to target period.

    Steps:
      1. Find all transactions in source period with status 'projected'.
      2. Update their pay_period_id to the target period.
      3. Flag template-linked items as is_override=True (they've been moved
         away from their rule-assigned period).
      4. Return the count of moved items.

    Args:
        source_period_id: The pay_period.id to carry forward FROM.
        target_period_id: The pay_period.id to carry forward TO.

    Returns:
        int — number of transactions moved.

    Raises:
        NotFoundError: If either period doesn't exist.
    """
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

    source = db.session.get(PayPeriod, source_period_id)
    if source is None:
        raise NotFoundError(f"Source pay period {source_period_id} not found.")

    target = db.session.get(PayPeriod, target_period_id)
    if target is None:
        raise NotFoundError(f"Target pay period {target_period_id} not found.")

    # Get the 'projected' status ID.
    projected_status = db.session.query(Status).filter_by(name="projected").one()

    # Find all non-deleted projected transactions in the source period.
    projected_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id == source_period_id,
            Transaction.status_id == projected_status.id,
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    count = 0
    for txn in projected_txns:
        txn.pay_period_id = target_period_id

        # If this transaction was auto-generated from a template, flag as override.
        if txn.template_id is not None:
            txn.is_override = True

        count += 1

    db.session.flush()
    log_event(logger, logging.INFO, "carry_forward", BUSINESS,
              "Carried forward unpaid items",
              count=count, from_period_id=source_period_id,
              to_period_id=target_period_id)
    return count
