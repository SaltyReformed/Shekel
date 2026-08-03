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
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    loan_loaders,
    loan_posting_service,
    loan_recurrence_sync,
)
from app.services._transfer_ownership import _get_owned_period
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


def _reject_transfer_out_of_loan(from_account: Account) -> None:
    """Reject a transfer whose SOURCE is an amortizing loan.

    A transfer OUT of a loan (the loan as ``from_account``) is not a modeled
    operation.  The amortization engine only projects payments INTO a loan, and
    the loan's posting ledger assumes every loan shadow is a payment IN: the
    per-payment interest / escrow split, the genesis reader's income-only
    history walk, and the reconciliation oracle's superseding invariant
    (``linked == settled_income_cash - per_loan_corrections``) all rest on it.
    A disbursement would instead post a raw cash movement onto the loan's linked
    ledger with no split correction and misproject every forward balance, so it
    is forbidden at the sole transfer-creation chokepoint
    (:func:`app.services.transfer_service.create_transfer`) rather than silently
    corrupting the loan's balance.

    Args:
        from_account: The transfer's source account (already ownership-checked).

    Raises:
        ValidationError: When *from_account* is an amortizing loan.
    """
    if classify_account(from_account) is AccountProjectionKind.AMORTIZING:
        raise ValidationError(
            f"Cannot transfer money out of a loan: source account "
            f"'{from_account.name}' is an amortizing loan."
        )


def _reject_payment_before_origination(
    to_account: Account, pay_period_id: int, due_date: date | None,
) -> None:
    """Reject a loan payment whose installment precedes the loan's origination.

    Ruling R-C (plan step C9b).  A loan cannot receive a payment before it
    exists, and the reason is not tidiness -- such a payment is ERASED, silently:

    * the fold orders events by installment and applies each anchor as a RESET,
      with a payment sorting BEFORE an anchor on a shared date, so a payment due
      at or before origination splits against a running balance of ZERO.
      ``split_payment_cash``'s closed-loan branch routes the entire cash to
      ``excess`` -- measured $0.00 interest, $0.00 principal, $1,200.00 to a
      Refund Receivable -- and the origination anchor then resets the balance to
      the full principal over the top of it.
    * meanwhile the cash side debits the funding account in full.

    So the money leaves checking, the loan is untouched, and the app models the
    lender as owing it back.  The developer ruled this REJECTED at the write
    boundary rather than modeled as a prepayment (which is a feature) or left to
    fail loud on read (which would 500 a page for data the user was allowed to
    enter).

    **The boundary is ``<=``, not ``<``.**  A payment due exactly ON the
    origination date is subsumed by that anchor's reset -- the same strict
    ``anchor_date < due_date`` post-anchor rule
    (:func:`~app.services.loan_ledger.merge_anchor_and_payment_events`) -- so it
    is erased identically.  Swept and measured: due 02-01, 02-28 and 03-01
    against a 03-01 origination all book $0.00 principal; 03-02 pays down
    $366.67.

    The installment is derived through the SHARED
    :func:`~app.services.loan_loaders.installment_for`, so the guard refuses
    exactly the payments the fold would erase -- including one carrying NO
    ``due_date``, whose installment comes from its pay-period start (an ad-hoc
    transfer into a loan, which is how the shape was originally found).

    A payment due AFTER origination but before the first contractual installment
    is deliberately ALLOWED: an early extra payment is legitimate and the fold
    splits it correctly against the opening balance.  This guard is about the
    loan's EXISTENCE; the recurrence ``start_date`` bound (C9a) is what keeps
    generated installments on the contract.

    A no-op for a non-loan destination and for an amortizing account with no
    :class:`~app.models.loan_params.LoanParams` yet (nothing to compare against
    -- ``classify_account`` reads the account TYPE only, so a Mortgage-typed
    account can be unconfigured).

    Args:
        to_account: The transfer's destination account (already
            ownership-checked).
        pay_period_id: The pay period the payment lands in (supplies the
            installment fallback when *due_date* is ``None``).
        due_date: The payment's due date, or ``None``.

    Raises:
        ValidationError: When *to_account* is a configured loan and the
            payment's installment falls at or before its origination.
    """
    if classify_account(to_account) is not AccountProjectionKind.AMORTIZING:
        return
    params = loan_loaders.load_loan_params(to_account.id)
    if params is None:
        return
    period = db.session.get(PayPeriod, pay_period_id)
    if period is None:
        # The caller's own ownership loader raises for a missing period; leave
        # that error to it rather than masking it with a different one.
        return
    installment = loan_loaders.installment_for(
        due_date, period.start_date, params.payment_day,
    )
    if installment > params.origination_date:
        return
    raise ValidationError(
        f"Cannot pay '{to_account.name}' before it originates: this payment's "
        f"installment ({installment.isoformat()}) falls on or before the loan's "
        f"origination date ({params.origination_date.isoformat()}).  Move the "
        f"payment to a later pay period, or correct the loan's origination date."
    )


# The ``update_transfer`` kwargs that can move WHICH INSTALLMENT a loan payment
# satisfies, and therefore whether it lands before its loan originated (ruling
# R-C, plan step C9b).  ``due_date`` names the installment directly;
# ``pay_period_id`` supplies the fallback basis for a payment carrying no due
# date (``loan_loaders.installment_for``).  Nothing else moves it -- an amount
# or status edit leaves the installment where it is -- so an edit touching
# neither is never re-checked, which is what keeps a pre-existing
# pre-origination row (legacy data the C9a purge deliberately left, e.g. a
# settled one) editable in every other respect.
_POSTING_RELEVANT_INSTALLMENT_FIELDS = frozenset({"pay_period_id", "due_date"})


def _reject_installment_move_before_loan(
    xfer: Transfer, user_id: int, updates: dict[str, object],
) -> None:
    """Refuse an ``update_transfer`` edit that drags a loan payment behind its loan.

    The edit-path half of ruling R-C (the create-path half is the
    :func:`_reject_payment_before_origination` call in
    :func:`create_transfer`).  Create is not the only door: the transfers PATCH
    route forwards ``due_date`` and ``pay_period_id`` straight through, so a
    payment created legitimately after origination could be moved behind it and
    then settled -- landing in exactly the erased state the create guard refuses
    (see that guard for what "erased" costs).

    Runs BEFORE any field is applied, so a rejected edit leaves the transfer and
    both shadows untouched -- the same discipline
    :func:`app.services.transfer_service._apply_status_to_all_three` follows
    for an illegal status transition.

    Only ``pay_period_id`` / ``due_date`` can move which installment a payment
    satisfies (:data:`_POSTING_RELEVANT_INSTALLMENT_FIELDS`), so an edit
    touching neither is never re-checked.  That is deliberate: it keeps a
    PRE-EXISTING pre-origination row -- legacy data the C9a purge intentionally
    leaves behind, such as a settled payment whose cash really moved -- editable
    in every other respect instead of frozen.

    A submitted ``pay_period_id`` is ownership-checked HERE, ahead of the guard,
    because the guard reads that period's ``start_date`` as the installment
    fallback: leaving the check to the caller's later ``pay_period_id`` block
    would let an unowned row answer this guard, returning a 400 carrying a date
    derived from it where the project's security-response rule requires an
    indistinguishable 404.  The later block's own call stays -- it is
    idempotent, and dropping it would make that section depend on this one.

    Args:
        xfer: The transfer being updated (supplies the current period / due date
            for any field the edit does not move, and the destination account).
        user_id: The acting user, for the pay-period ownership check.
        updates: The :func:`update_transfer` kwargs about to be applied --
            read, not written; *xfer* still holds its pre-edit values.

    Raises:
        NotFoundError: If a submitted ``pay_period_id`` is not the user's.
        ValidationError: If the resulting installment falls at or before the
            destination loan's origination.
    """
    if not _POSTING_RELEVANT_INSTALLMENT_FIELDS & updates.keys():
        return
    if "pay_period_id" in updates:
        _get_owned_period(updates["pay_period_id"], user_id)
    _reject_payment_before_origination(
        xfer.to_account,
        updates.get("pay_period_id", xfer.pay_period_id),
        updates.get("due_date", xfer.due_date),
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
    transfer's now-current settled state, in the transfer's own scenario -- BOTH
    the per-payment principal / interest / escrow split corrections
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
            xfer.to_account_id, xfer.scenario_id,
        )
        # R-4: an extra-principal payment shifts payoff earliest, so re-bound the
        # recurring payment's window to the new projected payoff (baseline).
        loan_recurrence_sync.sync_recurring_payment_bounds(
            xfer.to_account_id,
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
    (:func:`app.services.loan_posting_service.sync_loan_postings`) --
    re-splitting the LATER confirmed payments whose running balance the deletion
    changed AND re-deriving any true-up whose ``owed_before`` the deletion moved
    (deleting a pre-true-up payment).  Takes the loan / scenario ids explicitly
    because the caller has captured them before deleting the transfer (a
    hard-deleted ``xfer`` can no longer be read).

    Args:
        loan_account_id: The loan whose downstream ledger to re-reconcile.
        scenario_id: The deleted payment's scenario.
    """
    loan_posting_service.sync_loan_postings(loan_account_id, scenario_id)
    # R-4: deleting a payment moves the projected payoff, so re-bound the
    # recurring payment's window to it (baseline scenario).
    loan_recurrence_sync.sync_recurring_payment_bounds(loan_account_id)
