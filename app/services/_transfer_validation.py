"""
Shekel Budget App -- Transfer Service validate-and-load guards

The input-validation and entity-loading guards for
:mod:`app.services.transfer_service`: validate a submitted amount, load the
owned/active :class:`~app.models.transfer.Transfer`, and load-and-verify the
two shadow :class:`~app.models.transaction.Transaction` rows of a transfer.
Each is a precondition check the mutation entry points run before they touch
any row, raising the project's domain exceptions
(:class:`~app.exceptions.ValidationError` for a bad amount or a shadow-pair
integrity violation, :class:`~app.exceptions.NotFoundError` for a missing or
not-yours transfer -- with an identical message for both the "missing" and
the "not yours" case, the project security-response rule -- no existence
oracle).

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2-4 posting-ledger wiring lands -- the
same split that moved the ownership loaders into ``_transfer_ownership`` and
the loan-posting glue into ``_transfer_loan_posting``.  These three helpers
are a cohesive, transfer-service-private cluster (single responsibility:
validate inputs and load-and-verify the rows a mutation operates on) with no
dependency on the rest of the service, and they write no ``status_id`` and
construct no ``Transaction`` -- so they stay clear of the W9907 status fence
that keeps the status-mirroring appliers in the parent module, and they compute
no balance.
Flask-isolated like the parent service: plain data in, ORM objects out, no
``request`` / ``session`` imports.
"""

import logging
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


def _validate_positive_amount(amount):
    """Ensure *amount* is a positive Decimal.

    Args:
        amount: The transfer amount (Decimal, int, float, or string).

    Returns:
        The validated amount as a Decimal.

    Raises:
        ValidationError: If amount is zero, negative, or not numeric.
    """
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid amount: {amount!r}.  Must be a positive number."
        ) from exc
    if amount <= 0:
        raise ValidationError(
            "Transfer amount must be positive."
        )
    return amount


def _get_transfer_or_raise(transfer_id, user_id, allow_deleted=False):
    """Load a Transfer and verify ownership and active status.

    Args:
        transfer_id:   The primary key.
        user_id:       The expected owner.
        allow_deleted: If False (default), soft-deleted transfers are
                       treated as non-existent and raise NotFoundError.
                       Set to True for operations that legitimately need
                       to act on deleted transfers (e.g. delete_transfer
                       for idempotent soft-delete, restore_transfer).

    Returns:
        The Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist, belongs to
            another user, or is soft-deleted (when allow_deleted is
            False).  The message is identical in all cases (security
            response rule -- do not reveal existence to wrong user).
    """
    xfer = db.session.get(Transfer, transfer_id)
    if xfer is None or xfer.user_id != user_id:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    # Soft-deleted transfers are invisible to normal operations.
    # Without this check, update_transfer on a deleted transfer would
    # cascade into a misleading "0 shadow transactions" error from
    # _get_shadow_transactions (the shadows are also deleted).
    if not allow_deleted and xfer.is_deleted:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    return xfer


def _get_shadow_transactions(transfer_id):
    """Load shadow transactions for a transfer and identify types.

    Returns:
        Tuple (expense_shadow, income_shadow).

    Raises:
        ValidationError: If the shadow count is not exactly 2 or if
            both shadows have the same transaction type (data
            integrity violation).
    """
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id, is_deleted=False)
        .all()
    )

    if len(shadows) != 2:
        # Differentiate between a soft-deleted transfer (expected state,
        # not corruption) and a genuinely corrupt transfer missing
        # shadows (unexpected state).  _get_transfer_or_raise blocks
        # soft-deleted transfers by default, so this path should only
        # fire for real corruption -- but defense-in-depth means we
        # check anyway to produce a helpful diagnostic.
        xfer = db.session.get(Transfer, transfer_id)
        is_soft_deleted = xfer is not None and xfer.is_deleted

        shadow_ids = [s.id for s in shadows]
        if is_soft_deleted and len(shadows) == 0:
            logger.warning(
                "Transfer %d is soft-deleted.  Its shadow transactions "
                "are also soft-deleted and excluded from active queries.  "
                "This is expected, not data corruption.",
                transfer_id,
            )
            raise ValidationError(
                f"Transfer {transfer_id} is soft-deleted and cannot be "
                f"modified.  Use restore_transfer to reactivate it first."
            )

        # Genuine data integrity violation: transfer is active but has
        # the wrong number of shadows.  Fail-fast.
        logger.error(
            "Transfer %d has %d active shadow transactions (expected 2).  "
            "Shadow IDs: %s.  This indicates data corruption.",
            transfer_id, len(shadows), shadow_ids,
        )
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions instead of the expected 2.  "
            f"Data integrity issue -- cannot proceed."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)

    expense_shadow = None
    income_shadow = None
    for s in shadows:
        if s.transaction_type_id == expense_type_id:
            expense_shadow = s
        elif s.transaction_type_id == income_type_id:
            income_shadow = s

    if expense_shadow is None or income_shadow is None:
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Data integrity issue."
        )

    return expense_shadow, income_shadow
