"""
Shekel Budget App -- Transfer Service loan posting wiring

The genesis-ledger glue for :mod:`app.services.transfer_service`: the helpers
that re-reconcile a loan's full posting ledger -- the confirmed-payment split
corrections AND the opening / true-up anchor corrections
(:mod:`app.services.loan_posting_service`) -- whenever a transfer mutation
settles, reverts, edits, restores, or deletes a loan payment.

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the loan-posting wiring lands -- the same split that moved the
ownership loaders into ``_transfer_ownership``.  These helpers are a cohesive,
transfer-service-private cluster (single responsibility: keep the loan's genesis
ledger in step with a transfer mutation), routing every call through
:mod:`app.services.loan_posting_service` so ``transfer_service`` itself carries
no loan-posting knowledge.  Flask-isolated like the parent service: plain data
in, ORM objects or plain values out, no ``request`` / ``session``.

A loan payment is a Transfer whose ``to_account`` is an amortizing loan; its
income (to-account) shadow is where the payment-split correction books (by that
shadow's ``transaction_id``).  Every loan correction -- payment splits and
anchor corrections alike -- touches only the loan's own ledgers, never Checking,
so it is invisible to the Step-2 cash path.
"""

from datetime import date

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import loan_posting_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


def _income_shadow_for_transfer(xfer: Transfer) -> Transaction | None:
    """Return a transfer's loan-side income shadow, soft-deleted or not.

    The income (to-account) shadow, loaded WITHOUT the ``is_deleted`` filter so
    the delete path can reverse its Step-4 correction even on a hard delete of
    an already-soft-deleted transfer (whose shadows carry ``is_deleted=True``).
    ``None`` only for a corrupt transfer missing its income shadow, which the
    caller treats as "nothing to reverse".

    Args:
        xfer: The transfer whose income shadow to load.

    Returns:
        The loan-side income :class:`~app.models.transaction.Transaction`, or
        ``None`` if absent.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.transfer_id == xfer.id,
            Transaction.account_id == xfer.to_account_id,
            Transaction.transaction_type_id == income_type_id,
        )
        .one_or_none()
    )


def _sync_loan_postings_if_loan(xfer: Transfer) -> None:
    """Re-sync a loan's full genesis ledger after a settle / revert / edit / restore.

    When *xfer* pays down an amortizing loan, reconcile that loan's FULL genesis
    ledger (:func:`app.services.loan_posting_service.sync_loan_postings`) to the
    transfer's now-current settled state, in the transfer's own scenario, as of
    today -- BOTH the per-payment principal / interest / escrow split corrections
    AND the opening / true-up anchor corrections.  The anchor half matters
    because a change to a payment that came due BEFORE a true-up moves that
    true-up's ``owed_before`` (the running balance it corrects from), so the
    true-up self-heals in the same reconcile; and every payment's split rides
    the same running balance, so this is a whole-loan reconcile, not a
    per-payment one.  A no-op for a non-loan transfer (the common case), so the
    settle / revert / restore chokepoints call it unconditionally after the
    Step-2 cash reconcile.

    Every correction touches only the loan's own ledgers (never Checking) -- the
    payment corrections link by the loan-side income shadow's ``transaction_id``
    and the anchor corrections carry a NULL transfer_id / transaction_id -- so
    the whole reconcile is structurally invisible to the Step-2 cash path and
    cannot move a cash balance (plan Section 5 / 7).

    Args:
        xfer: The transfer just mutated.  Its ``to_account`` (with
            ``account_type``) drives the amortizing-loan classification and its
            ``scenario_id`` scopes the reconcile.
    """
    if classify_account(xfer.to_account) is AccountProjectionKind.AMORTIZING:
        loan_posting_service.sync_loan_postings(
            xfer.to_account_id, xfer.scenario_id, date.today(),
        )


def _reverse_loan_payment_before_delete(xfer: Transfer) -> bool:
    """Reverse a loan payment's split correction before its transfer is deleted.

    When *xfer* pays an amortizing loan, reconcile the deleted payment's Step-4
    correction to zero
    (:func:`app.services.loan_posting_service.reverse_loan_payment_postings_for_shadow`)
    while the income shadow's id still exists -- load-bearing for a HARD delete,
    whose ``ON DELETE SET NULL`` on ``journal_entries.transaction_id`` would
    otherwise strand the correction's legs once the shadow row is gone.
    Mirrors the Step-2 cash reverse-before-delete already run at the delete
    chokepoint.

    Args:
        xfer: The transfer about to be deleted.

    Returns:
        ``True`` when *xfer* pays an amortizing loan (so the caller re-splits
        the downstream payments after the row is removed via
        :func:`_resync_loan_postings_after_delete`), ``False``
        otherwise.
    """
    if classify_account(xfer.to_account) is not AccountProjectionKind.AMORTIZING:
        return False
    income_shadow = _income_shadow_for_transfer(xfer)
    if income_shadow is not None:
        loan_posting_service.reverse_loan_payment_postings_for_shadow(
            income_shadow,
        )
    return True


def _resync_loan_postings_after_delete(
    loan_account_id: int, scenario_id: int,
) -> None:
    """Re-sync a loan's downstream ledger after one payment is deleted.

    Run AFTER the deleted transfer's row is gone (its payment correction already
    reversed by :func:`_reverse_loan_payment_before_delete`): re-reconciles the
    loan's full genesis ledger
    (:func:`app.services.loan_posting_service.sync_loan_postings` as of today) --
    re-splitting the LATER confirmed payments whose running balance the deletion
    changed AND re-deriving any true-up whose ``owed_before`` the deletion moved
    (deleting a pre-true-up payment).  Takes the loan / scenario ids explicitly
    because the caller has captured them before deleting the transfer (a
    hard-deleted ``xfer`` can no longer be read).

    Args:
        loan_account_id: The loan whose downstream ledger to re-reconcile.
        scenario_id: The deleted payment's scenario.
    """
    loan_posting_service.sync_loan_postings(
        loan_account_id, scenario_id, date.today(),
    )
