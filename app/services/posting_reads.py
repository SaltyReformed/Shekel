"""
Shekel Budget App -- Posting Ledger Read-Side Helpers

The read-side companions of :mod:`app.services.posting_service` (the ledger's
sole writer), extracted when the writer crossed the module-size gate -- the
same sibling-split the loan posting package and ``transfer_service._loan_posting``
follow.  Three concerns live here:

* :class:`PostingError` -- the shared invariant-violation error both sides
  raise;
* :func:`_ledger_account_for` -- the chart-of-accounts pairing lookup the
  writer's target builders and the readers share;
* the reconciliation readers (:func:`account_posting_total`,
  :func:`settled_transfer_effect`, :func:`settled_transaction_effect`) -- the
  oracle-facing sums the integration oracles pit against each other.

``posting_service`` re-exports all five names, so every existing consumer
(the oracles, the loan posting package) keeps reading them off the writer
module -- the ledger's one public surface.

**Flask-isolated** and read-only: plain data in, plain values out; never
imports ``request`` / ``session``; performs no writes.
"""

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import LedgerAccountKindEnum, TxnTypeEnum
from app.exceptions import ShekelError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.utils.balance_predicates import settled_status_ids


class PostingError(ShekelError):
    """A posting-ledger invariant was violated and the write was refused.

    Raised for the should-never-happen data-integrity failures the posting
    services guard (a real account with no paired ledger account; a settled
    transfer with no active income shadow; a caller-supplied set of legs that
    does not balance; a ``None`` scenario in a reconciliation helper).  These
    are not user-input errors -- the chart-of-accounts pairing and the
    two-shadow transfer invariant are guaranteed upstream -- so a violation
    here means a broken invariant that must fail loudly rather than post a
    wrong or unbalanced entry.
    """


def _ledger_account_for(account_id: int) -> LedgerAccount:
    """Return the LINKED ledger account paired with a real account, or fail loudly.

    Every ``budget.accounts`` row has exactly one linked ledger account (the
    Commit-2 create hook pairs new accounts; the Commit-2 backfill paired
    historical ones; ``uq_ledger_accounts_account_kind`` permits only one per
    kind).  The ``linked``-kind filter is load-bearing since Step 5: an
    account may ALSO carry an ``anchor_equity`` twin on the same
    ``account_id``, and an unfiltered ``one_or_none`` would raise
    ``MultipleResultsFound`` the moment the twin exists.  A missing pairing
    is a broken chart-of-accounts invariant, not a benign lookup miss, so
    this raises rather than returning ``None``.

    Args:
        account_id: The real account whose linked ledger account to load.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`.

    Raises:
        PostingError: If no ledger account is linked to *account_id*.
    """
    ledger = (
        db.session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
        )
        .one_or_none()
    )
    if ledger is None:
        raise PostingError(
            f"No ledger account is linked to account {account_id}; the "
            f"chart-of-accounts pairing is missing (every account is paired "
            f"by the account-create hook or the Step-2 backfill)."
        )
    return ledger


def account_posting_total(account_id: int, scenario_id: int) -> Decimal:
    """Return the net of all posting legs on an account's ledger in a scenario.

    Sums ``account_postings.amount`` over the account's linked ledger account
    for journal entries in *scenario_id* (the ``scenario_id`` denorm on the
    entry keeps scenarios isolated).  This is the ledger side of the Commit-6
    reconciliation oracle; it equals :func:`settled_transfer_effect` for the
    same account and scenario when the ledger is in sync.

    Args:
        account_id: The real account whose ledger postings to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net of the account's posting legs as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None`` (a scenario is required to
            isolate the sum), or the account has no linked ledger account.
    """
    if scenario_id is None:
        raise PostingError(
            "account_posting_total requires a scenario_id (postings are "
            "scenario-scoped); got None."
        )
    ledger = _ledger_account_for(account_id)
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger.id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def settled_transfer_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled transfer shadows.

    The balance-side expectation the Commit-6 oracle reconciles the ledger
    against: over the account's settled (``status.is_settled``), non-deleted
    transfer shadows in *scenario_id*, sum ``+effective_amount`` for an income
    shadow (money in) and ``-effective_amount`` for an expense shadow (money
    out) -- exactly the debit-positive net :func:`account_posting_total`
    accumulates.  ``effective_amount`` is ``COALESCE(actual, estimated)``;
    settled statuses are non-excluded by construction (``settled_status_ids``
    is disjoint from the balance-excluded set), so no excluded-status guard is
    needed.

    Args:
        account_id: The real account whose settled transfer shadows to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled transfer shadows as a
        ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "settled_transfer_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = db.func.coalesce(
        Transaction.actual_amount, Transaction.estimated_amount
    )
    signed_effect = case(
        (Transaction.transaction_type_id == income_type_id, effective),
        else_=-effective,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def settled_transaction_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled ordinary transactions.

    The transaction analog of :func:`settled_transfer_effect`, and the
    balance-side expectation the Build-Order Step 3 reconciliation oracle
    reconciles the ledger against: over the account's settled
    (``status.is_settled``), non-deleted, NON-transfer (``transfer_id IS
    NULL``) transactions in *scenario_id*, sum the signed confirmed cash effect
    ``effective - Sigma(credit entries)`` -- ``+`` for income (money in), ``-``
    for an expense (money out) -- exactly the debit-positive net the cash legs
    accumulate via :func:`account_posting_total`.  ``effective`` is
    ``COALESCE(actual, estimated)``; the per-transaction credit-entry sum is a
    correlated subquery (the SQL counterpart of the go-forward
    ``credit_entry_sum``).  Settled statuses are non-excluded by construction
    (``settled_status_ids`` is disjoint from the balance-excluded set), so no
    excluded-status guard is needed.

    For a real account A, ``account_posting_total(A) ==
    settled_transfer_effect(A) + settled_transaction_effect(A)`` once the
    ledger is in sync (the oracle's per-account invariant).

    Args:
        account_id: The real account whose settled transactions to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled ordinary transactions
        as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "settled_transaction_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    effective = db.func.coalesce(
        Transaction.actual_amount, Transaction.estimated_amount
    )
    # Per-transaction sum of credit-card entry amounts, correlated to the outer
    # transaction so it excludes the credit portion exactly as the go-forward
    # ``credit_entry_sum`` does (the CC Payback posts that portion separately).
    credit_sum = (
        db.session.query(
            db.func.coalesce(db.func.sum(TransactionEntry.amount), Decimal("0"))
        )
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.is_credit.is_(True),
        )
        .correlate(Transaction)
        .scalar_subquery()
    )
    cash_effect = effective - credit_sum
    signed_effect = case(
        (Transaction.transaction_type_id == income_type_id, cash_effect),
        else_=-cash_effect,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )
