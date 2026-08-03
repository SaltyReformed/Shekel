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
the loan-posting glue into ``_transfer_loan_posting``.  :func:`assert_restorable`
joined them at plan step X-aj1 (ruling **R-DR**), bringing ``restore_transfer``'s
four preconditions to the module whose single responsibility they already were.

These FOUR helpers are a cohesive, transfer-service-private cluster (single
responsibility: validate inputs and load-and-verify the rows a mutation operates
on).  They write no ``status_id`` and construct no ``Transaction`` -- so they stay
clear of the W9907 status fence that keeps the status-mirroring appliers in the
parent module -- and they compute no balance.  :func:`assert_restorable` READS the
state machine (:func:`~app.services.state_machine.allowed_transitions`) to decide
whether a drifted shadow is legally repairable; reading the transition rules is
not writing a status, so the fence is unaffected.
Flask-isolated like the parent service: plain data in, ORM objects out, no
``request`` / ``session`` imports.
"""

import logging
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.services.state_machine import allowed_transitions
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    log_event,
)

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


def assert_restorable(xfer, shadows, user_id):
    """Refuse a restore whose preconditions do not hold, before anything moves.

    The four checks ``restore_transfer`` runs before it un-deletes a thing.
    Extracted here at plan step X-aj1 (ruling **R-DO**) because they are
    precondition checks on the rows a mutation operates on, which is this
    module's single responsibility, and because gathering them made the caller's
    own defect visible: it used to set ``is_deleted = False`` FIRST and then
    hand-restore the flag on each failing branch -- a rollback written out three
    times, with three chances for the next branch to forget it.  Validating
    before mutating makes that class of miss structurally impossible, and the
    three hand-rollbacks are deleted rather than extended to a fourth.

    The checks, in order, each refusing rather than repairing:

    1. **Shadow count.** Exactly two, or the pair is corrupt (Invariant 1).
    2. **Type pairing.** One expense and one income, or the pair is corrupt.
    3. **Archived endpoints (F-164).** The account FK is RESTRICT, so the rows
       cannot be hard-deleted while the transfer references them; the only way
       an endpoint goes away semantically is ``is_active = False``.  Restoring
       onto one would resurrect entries against an account the user has
       withdrawn from active projections, producing balance drift they have no
       UI affordance to investigate.
    4. **Unrepairable status drift (ruling R-DO).** A shadow whose status the
       state machine cannot legally move to the parent's is corruption, not
       drift.  It used to be rewritten with no transition check at all, which
       destroys the evidence of how it happened -- and a settled shadow silently
       reverted to Projected would strand its postings.  It is refused in the
       same voice as checks 1 and 2, which is what makes the three consistent.

    Args:
        xfer: The soft-deleted :class:`~app.models.transfer.Transfer` being
            restored.  NOT mutated here.
        shadows: Every :class:`~app.models.transaction.Transaction` linked to
            it, loaded without an ``is_deleted`` filter.
        user_id: The owner, for the archived-endpoint refusal's structured log.

    Raises:
        ValidationError: On any of the four, with a message naming what a human
            has to fix.
    """
    transfer_id = xfer.id
    if len(shadows) != 2:
        logger.error(
            "Cannot restore transfer %d: expected 2 shadow transactions, "
            "found %d.  Shadow IDs: %s.  Data integrity issue.",
            transfer_id, len(shadows), [s.id for s in shadows],
        )
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions (expected 2).  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    type_ids = {s.transaction_type_id for s in shadows}
    if type_ids != {expense_type_id, income_type_id}:
        logger.error(
            "Cannot restore transfer %d: shadow type pairing is invalid.  "
            "Expected one expense and one income, found type_ids=%s.",
            transfer_id, type_ids,
        )
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Cannot restore -- data "
            f"integrity issue requiring manual intervention."
        )

    from_account = db.session.get(Account, xfer.from_account_id)
    to_account = db.session.get(Account, xfer.to_account_id)
    from_active = bool(from_account is not None and from_account.is_active)
    to_active = bool(to_account is not None and to_account.is_active)
    if not (from_active and to_active):
        log_event(
            logger, logging.WARNING,
            EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT, BUSINESS,
            "Refused to restore transfer with archived account",
            user_id=user_id,
            transfer_id=transfer_id,
            from_account_id=xfer.from_account_id,
            to_account_id=xfer.to_account_id,
            from_account_active=from_active,
            to_account_active=to_active,
        )
        raise ValidationError(
            "Cannot restore transfer: source or destination account "
            "is archived.  Reactivate the account before restoring."
        )

    unrepairable = {
        shadow.id: shadow.status_id for shadow in shadows
        if shadow.status_id != xfer.status_id
        and xfer.status_id not in allowed_transitions(shadow)
    }
    if unrepairable:
        logger.error(
            "Cannot restore transfer %d: shadow(s) %s hold a status that "
            "cannot legally reach the transfer's status %s.  Data "
            "integrity issue.",
            transfer_id, unrepairable, xfer.status_id,
        )
        raise ValidationError(
            f"Transfer {transfer_id} has a shadow transaction whose status "
            f"cannot legally be reconciled with the transfer's.  Cannot "
            f"restore -- data integrity issue requiring manual intervention."
        )
