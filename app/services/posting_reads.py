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
from app.enums import SettlementBasisEnum, TxnTypeEnum
from app.exceptions import ShekelError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    settled_status_ids,
)


def settled_figure_clause():
    """Return the SQL for what a SETTLED transaction records as having moved.

    The query-tier twin of :func:`app.services.row_valuation.settled_figure`, and
    the ONE spelling of it in SQL (plan step **X-au-c3**): three folds ask it --
    :func:`settled_transfer_effect`, :func:`settled_transaction_effect` and
    ``posting_service._settle_effective`` -- and three copies of a money rule is
    this arc's own root cause 1.

    A ``CASE`` on ``settled_basis_id``, which is the SAME column the Python twin
    dispatches on.  A ``purchases`` record stores no figure and sums the row's
    entries; every other basis stores its figure in ``settled_amount``.

    **It reads the basis rather than testing ``settled_amount IS NULL``, and
    that is a defect fixed rather than a style choice.**  The expression was
    ``COALESCE(settled_amount, Sigma(entries))``, which is right for every
    WELL-FORMED row -- the two states are disjoint by
    :class:`app.services.status_seam.Settlement`'s constructor -- and wrong for
    the one row that is not: a settled row recording NOTHING has no stored
    figure and (typically) no entries, so the ``COALESCE`` answered ``0`` where
    :func:`app.services.row_valuation.settled_figure` RAISES.  A refusal on one
    tier and a zero on the other is money leaving a balance in silence, and it
    is the SQL side that writes the ledger.  Dispatching on the basis makes the
    broken row take NO arm and answer ``NULL``, which a fold drops and
    ``posting_service._settle_effective`` refuses -- and
    ``ck_transactions_settle_day_needs_basis`` is what makes it unstorable in the
    first place, so this arm is the belt to that constraint's braces rather than
    the only guard.

    **What it replaced was a fallback, and the difference is the point.**  This
    read was ``COALESCE(actual_amount, estimated_amount)`` -- the settled figure
    falling back to the row's PLAN, because ``actual_amount`` was populated only
    when a human had typed a correction.  Two consequences followed, and both are
    why the FREEZE this step was originally specified to build existed at all: a
    plan is a derivation, so the fold's answer for a historical row could move
    when a price series gained a backdated version; and once a per-kind cutover
    (plan steps X-au-d..X-au-i) emptied that plan, a ``SUM`` over the expression
    would DROP the row silently rather than raise -- the undercount findings
    **N-242** and **N-298** describe.  Neither is reachable now: a settled row's
    figure is its own record, and a row with no record has not settled.

    Callers must still filter to settled rows themselves.  A ``purchases`` row
    with no entries answers ``0`` rather than ``NULL`` -- the entry sum's own
    ``COALESCE`` -- and that is correct: an envelope closed empty cost nothing,
    which is what its records say.

    Returns:
        A SQLAlchemy expression over :class:`~app.models.transaction.Transaction`
        evaluating to the recorded figure, or ``NULL`` for a row that records no
        settlement at all.
    """
    purchases_sum = (
        db.session.query(
            db.func.coalesce(db.func.sum(TransactionEntry.amount), Decimal("0"))
        )
        .filter(TransactionEntry.transaction_id == Transaction.id)
        .correlate(Transaction)
        .scalar_subquery()
    )
    purchases_basis_id = ref_cache.settlement_basis_id(
        SettlementBasisEnum.PURCHASES,
    )
    return case(
        (Transaction.settled_basis_id.is_(None), None),
        (Transaction.settled_basis_id == purchases_basis_id, purchases_sum),
        else_=Transaction.settled_amount,
    )


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
    kind).  A missing pairing is a broken chart-of-accounts invariant, not a
    benign lookup miss, so this raises rather than returning ``None`` -- which
    is the WHOLE of what this adds over the chart's own lookup
    (:func:`app.services.ledger_account_service.find_linked_ledger_account`).

    **The query itself lives with the chart, not here** (plan step X-f3d).
    The ``linked``-kind filter is load-bearing since Step 5 -- an account may
    ALSO carry per-account counter rows on the same ``account_id``, and an
    unfiltered ``one_or_none`` would raise ``MultipleResultsFound`` the moment
    one exists -- and it was spelled out THREE times across two modules, so a
    reader and a writer could come to disagree about which row is an account's
    own.  The ``duplicate-code`` gate is what surfaced the third copy.

    Args:
        account_id: The real account whose linked ledger account to load.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`.

    Raises:
        PostingError: If no ledger account is linked to *account_id*.
    """
    ledger = ledger_account_service.find_linked_ledger_account(account_id)
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
    transfer shadows in *scenario_id*, sum ``+``:func:`settled_figure_clause`
    for an income shadow (money in) and the negation for an expense shadow
    (money out) -- exactly the debit-positive net
    :func:`account_posting_total` accumulates.  It read
    ``COALESCE(actual_amount, estimated_amount)`` until plan step X-au-c3
    renamed that column and made a settled row's figure its own RECORD, so the
    shadow's plan is no longer consulted for it at all;
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
    effective = settled_figure_clause()
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
    :func:`settled_figure_clause` -- what the row RECORDED as having moved, not
    its plan, since plan step X-au-c3; the per-transaction credit-entry sum is a
    correlated subquery (the SQL counterpart of the go-forward
    ``credit_entry_sum``).  Settled statuses are non-excluded by construction
    (``settled_status_ids`` is disjoint from the balance-excluded set), so no
    excluded-status guard is needed.

    **It does NOT subtract the row's already-posted purchases, and that is what
    keeps it an ORACLE** (ruling **R-FM**, plan step X-f3b).  Since that step a
    settled envelope's own leg books ``effective - credit - posted purchases``
    while each posted purchase books its own leg, so the FAMILY still sums to
    ``effective - credit`` -- which is what this expression already computes.
    Restating the split here would make the oracle share the implementation it
    grades; leaving it whole makes it grade the split's own arithmetic.

    For a real account A, ``account_posting_total(A) ==
    settled_transfer_effect(A) + settled_transaction_effect(A) +
    posted_purchase_effect(A)`` once the ledger is in sync (the oracle's
    per-account invariant).  The third term covers the purchases whose PARENT is
    not settled, which no ``effective`` figure contains.

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
    effective = settled_figure_clause()
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


def posted_purchase_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from purchases on UNSETTLED parents.

    The third term of the oracle's per-account invariant, and ruling **R-FM**
    is why it exists (plan step X-f3b).  A purchase whose bank posting day is
    recorded books its own cash leg, so an account's posted total now contains
    money that no settled row's ``effective`` figure accounts for: the
    purchases whose PARENT is still Projected.  A purchase on a SETTLED parent
    is already inside :func:`settled_transaction_effect` -- that expression sums
    ``effective - credit`` over the whole row, which is exactly what the parent
    leg and its purchases' legs sum to -- so counting one here would
    double-count it.

    Over the account's non-deleted, balance-contributing, NON-transfer
    transactions in *scenario_id* that are NOT settled: sum ``-amount`` over
    their debit entries carrying a ``settled_on``.  Always negative or zero: a
    purchase is an expense and its money leaves.

    **The three narrowings are the write side's, restated in SQL rather than
    shared with it** -- the same deliberate independence
    :func:`settled_transaction_effect` keeps.  An oracle that imported
    ``posting_service._purchase_posts`` could not grade it.

    Args:
        account_id: The real account whose posted purchases to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "posted_purchase_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    return (
        db.session.query(
            db.func.coalesce(
                db.func.sum(-TransactionEntry.amount), Decimal("0")
            )
        )
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.notin_(
                # NOT settled and NOT excluded: the parents whose own
                # ``effective`` figure is in no other term of the invariant.
                set(settled_status_ids()) | set(balance_excluded_status_ids())
            ),
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .scalar()
    )
