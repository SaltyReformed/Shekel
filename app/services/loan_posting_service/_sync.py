"""Loan posting orchestration: all-scenarios sync, duplicate translation, backfill.

The loan-GLOBAL entry points that drive the per-scenario payment sync
(:func:`app.services.loan_posting_service.sync_loan_payment_postings`) across every
scenario a loan has payments in -- a balance true-up, a rate change, and a
params edit all live on the loan ACCOUNT, not a scenario, so they re-base the
confirmed-payment split in every scenario at once.  Also the one-time historical
backfill.  Flushes but never commits -- the caller owns the transaction boundary.
"""

from datetime import date

from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import loan_payment_service
from app.utils.db_errors import is_unique_violation

from ._payments import sync_loan_payment_postings


def _scenarios_with_loan_payments(loan_account_id: int) -> list[int]:
    """Return the scenarios that carry a payment shadow for a loan.

    The distinct ``scenario_id`` set over the loan's non-deleted income shadows
    (transfer-linked, Income type) -- the scenarios whose split corrections a
    loan-GLOBAL change re-bases.  A balance true-up, a rate change, and a
    params edit all live on the loan ACCOUNT, not a scenario, so they move the
    confirmed-payment split in every scenario the loan has payments in;
    :func:`sync_loan_payment_postings_all_scenarios` reconciles each in turn.
    A projected-only scenario is harmlessly included -- its sync is a no-op,
    since only a settled payment posts a correction.

    Args:
        loan_account_id: The loan whose payment scenarios to enumerate.

    Returns:
        The distinct scenario ids, ascending.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    rows = (
        db.session.query(Transaction.scenario_id)
        .filter(
            Transaction.account_id == loan_account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
        )
        .distinct()
        .all()
    )
    return sorted(row[0] for row in rows)


def sync_loan_payment_postings_all_scenarios(loan_account_id: int) -> None:
    """Reconcile a loan's split corrections across EVERY scenario, as of today.

    The loan-GLOBAL chokepoint entry point (a balance true-up, a rate change, a
    loan-params create / edit): the anchor and rate live on the loan account,
    not the scenario, so such a change re-bases the confirmed-payment split in
    every scenario the loan has payments in.  Loops
    :func:`_scenarios_with_loan_payments` through
    :func:`sync_loan_payment_postings` as of ``date.today()`` -- the same as-of
    the loan reads use
    (:func:`app.services.loan_payment_service.resolve_account_loan`).

    A brand-new or unresolvable loan (no confirmed payments) syncs nothing.
    Idempotent and self-healing: a re-run at the same state writes nothing.
    Flushes but does not commit (the caller owns the transaction).

    Args:
        loan_account_id: The loan whose corrections to reconcile across every
            scenario it has payments in.
    """
    as_of = date.today()
    for scenario_id in _scenarios_with_loan_payments(loan_account_id):
        sync_loan_payment_postings(loan_account_id, scenario_id, as_of)


def sync_all_scenarios_or_duplicate(
    loan_account_id: int, unique_index_name: str,
) -> bool:
    """Re-split a loan across scenarios and flush, reporting a same-key duplicate.

    The shared body of the two loan-GLOBAL chokepoints that append a
    unique-constrained row and THEN re-split -- the balance true-up (which adds
    a :class:`~app.models.loan_anchor_event.LoanAnchorEvent`) and the ARM rate
    change (which adds a :class:`~app.models.loan_features.RateHistory` row).
    Runs :func:`sync_loan_payment_postings_all_scenarios` (whose queries
    autoflush the caller's just-added row) then an explicit flush, so a same-key
    duplicate the pending row collides on surfaces HERE -- inside one ``try`` --
    and is translated to a ``False`` return instead of leaking as a 500 at a
    later, unguarded commit.  The sync and the flush share the ``try`` precisely
    because the sync's autoflush is what triggers the pending row's INSERT.

    Flushes but does NOT commit (the caller owns the transaction and its
    outcome / response handling).  On a duplicate it rolls back -- discarding
    the duplicate row and the sync's work together -- exactly as the caller's
    prior idempotent commit did; the prior committed state stands.

    Args:
        loan_account_id: The loan whose corrections to reconcile across every
            scenario it has payments in.
        unique_index_name: The unique index / constraint the caller's pending
            row can collide on (``uq_loan_anchor_events_acct_date_bal_day`` for
            a true-up, ``uq_rate_history_account_effective_date`` for a rate
            change).

    Returns:
        ``True`` when the sync + flush succeeded (the caller should commit);
        ``False`` when the named unique index rejected the pending row -- an
        idempotent same-key duplicate, already rolled back.

    Raises:
        IntegrityError: For any ``IntegrityError`` NOT on *unique_index_name* --
            an unexpected constraint failure must surface, never be swallowed.
    """
    try:
        sync_loan_payment_postings_all_scenarios(loan_account_id)
        db.session.flush()
        return True
    except IntegrityError as exc:
        db.session.rollback()
        if not is_unique_violation(exc, unique_index_name):
            raise
        return False


def backfill_all_loan_payment_postings() -> list[int]:
    """Reconcile every loan's split corrections across all scenarios (the backfill).

    The one-time, production-wide historical backfill: for every configured loan
    account, across all owners
    (:func:`app.services.loan_payment_service.load_all_loan_account_ids`),
    reconcile its confirmed payment corrections to the real split via
    :func:`sync_loan_payment_postings_all_scenarios`.  This posts the correction
    for any payment settled BEFORE the go-forward wiring shipped (which therefore
    carries none), so the ledger is complete on real historical data.

    Reuses the SAME per-loan sync the go-forward chokepoints call, so a
    backfilled correction is identical to the go-forward one by construction --
    there is no second split implementation that could drift from it.  Idempotent
    and self-healing via reconcile-to-target: a payment that already carries a
    go-forward correction is already at target, so nothing is re-posted -- the
    backfill never double-posts, and a re-run at the same state writes nothing.

    Flushes but does NOT commit -- the caller owns the transaction boundary: the
    deploy hook
    (``scripts.init_database.backfill_loan_payment_postings_after_migration``,
    which initialises ``ref_cache`` first because the migration host does not),
    the backfill suite, or the reconciliation oracle.

    Returns:
        The loan account ids reconciled, ascending -- for the deploy log and
        test introspection (empty on a loan-free database).
    """
    loan_account_ids = loan_payment_service.load_all_loan_account_ids()
    for loan_account_id in loan_account_ids:
        sync_loan_payment_postings_all_scenarios(loan_account_id)
    return loan_account_ids
