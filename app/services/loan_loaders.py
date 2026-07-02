"""
Shekel Budget App -- Loan Data Loaders (the loan services' leaf layer)

The pure data-loading functions every loan consumer shares: the
:class:`LoanParams` / :class:`LoanAnchorEvent` / :class:`RateHistory` /
:class:`EscrowComponent` row loaders and the shadow-income query builder.
Extracted from :mod:`app.services.loan_payment_service` (the read switch's
final arc) so the loan POSTING package and the loan PAYMENT service both
depend on one leaf module instead of on each other: the posting package's
walk and reader need these loaders, while ``loan_payment_service`` hosts the
read-switch seam that imports the posting package's reader -- loading through
a shared leaf is what keeps that dependency one-directional (no import
cycle), rather than a lazy-import workaround.

This module is a LEAF: it imports models, the engine's plain
:class:`~app.services.amortization_engine.RateChangeRecord` record, and the
shared balance predicates -- never another loan service.  Flask-isolated,
reads only, no commits.

This service queries ONLY budget.transactions (transfer invariant #5).
It NEVER queries budget.transfers.
"""

from decimal import Decimal

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services.amortization_engine import RateChangeRecord
from app.utils.balance_predicates import balance_excluded_status_ids


def _rate_change_records_from(
    rate_history_records: list,
) -> list[RateChangeRecord] | None:
    """Convert loaded RateHistory rows to the engine's RateChangeRecord feed.

    The pure (no-DB) half of rate-change loading, shared by
    :func:`app.services.loan_payment_service.load_loan_context` (which also
    keeps the raw ORM rows for its ``rate_history`` display field) and
    :func:`load_rate_changes` (which needs only the feed), so the two cannot
    drift on how a :class:`RateHistory` row maps to a
    :class:`RateChangeRecord`.  Returns ``None`` -- not an empty list -- for no
    rows: the resolver treats ``None`` and an empty feed identically (an
    origination-row-less loan is unresolvable), and the explicit ``None``
    keeps the established contract a loan with no RateHistory has no feed at
    all.

    Args:
        rate_history_records: The loan's :class:`RateHistory` ORM rows (any
            order; each exposes ``effective_date`` / ``interest_rate`` /
            optional ``monthly_pi``).

    Returns:
        The :class:`RateChangeRecord` list, or ``None`` when there are no rows.
    """
    if not rate_history_records:
        return None
    return [
        RateChangeRecord(
            effective_date=rh.effective_date,
            interest_rate=Decimal(str(rh.interest_rate)),
            monthly_pi=(
                Decimal(str(rh.monthly_pi))
                if rh.monthly_pi is not None else None
            ),
        )
        for rh in rate_history_records
    ]


def load_rate_history(account_id: int) -> list:
    """Load a loan's raw :class:`RateHistory` rows, newest first.

    The one query definition behind BOTH rate-history consumers: the
    feed-only loader (:func:`load_rate_changes`) and
    :func:`app.services.loan_payment_service.load_loan_context`, which keeps
    the raw ORM rows for its ``rate_history`` display field alongside the
    mapped feed -- so the two cannot drift on how a loan's rate history is
    read (ordering, soft-delete handling).

    Args:
        account_id: The loan account whose rate history to load.

    Returns:
        The account's :class:`RateHistory` rows, ``effective_date`` DESC
        (possibly empty for an unconfigured loan).
    """
    return (
        db.session.query(RateHistory)
        .filter_by(account_id=account_id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )


def load_rate_changes(account_id: int) -> list[RateChangeRecord] | None:
    """Load a loan's rate-change feed (origination row plus any ARM adjustments).

    Queries the account's :class:`RateHistory` rows (newest first, the same
    order :func:`app.services.loan_payment_service.load_loan_context` uses)
    and maps them to the engine's :class:`RateChangeRecord` feed via
    :func:`_rate_change_records_from`.  The standalone loader for callers that
    need ONLY the feed -- the Build-Order Step 4 split walk
    (:func:`app.services.loan_posting_service.compute_loan_payment_splits`)
    builds the loan's rate periods from it via
    :func:`app.services.loan_resolver.resolve_periods` -- without paying for
    the rest of ``load_loan_context``'s payment-history / escrow /
    contractual-P&I work.

    Args:
        account_id: The loan account whose rate history to load.

    Returns:
        The :class:`RateChangeRecord` list (newest first), or ``None`` when the
        loan carries no :class:`RateHistory` row (an origination-row-less,
        unresolvable loan -- the resolver raises on such a feed).
    """
    return _rate_change_records_from(load_rate_history(account_id))


def load_loan_params(account_id: int) -> LoanParams | None:
    """Load a loan account's :class:`LoanParams` row, or None.

    The one-line "is this a configured loan, and if so what are its terms"
    lookup shared by every loan consumer
    (:func:`app.services.loan_payment_service.resolve_account_loan`, the loan
    PITI resolver, and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`), so
    none of them re-spells the same query and a future change to how a loan's
    params are loaded (eager-loads, soft-delete handling) touches one site.
    ``None`` means the account has no loan configuration yet -- not an
    amortizing loan, or a loan whose setup is incomplete -- and the caller
    short-circuits.

    Args:
        account_id: The account whose loan parameters to load.

    Returns:
        The :class:`LoanParams` row, or ``None`` when the account is not a
        configured loan.
    """
    return (
        db.session.query(LoanParams)
        .filter_by(account_id=account_id)
        .first()
    )


def load_all_loan_account_ids() -> list[int]:
    """Return every configured loan account's id, ascending (all owners).

    The account id of every :class:`LoanParams` row -- one per amortizing loan,
    across all owners.  A loan can carry a Build-Order Step 4 split correction
    only once it has a :class:`LoanParams` row (:func:`load_loan_params`;
    :func:`app.services.loan_posting_service.compute_loan_payment_splits` returns
    ``[]`` otherwise), so this is exactly the set the one-time historical backfill
    (:func:`app.services.loan_posting_service.backfill_all_loan_postings`)
    iterates.  Deliberately NOT user-scoped: it is a system / deploy-time sweep
    over every owner's loans -- like the Step-2 / Step-3 settled-row backfills --
    and each posted correction still carries its own owner (from the payment
    shadow's pay period), so no row is mis-attributed.

    Returns:
        The loan account ids, ascending (``account_id`` is unique per
        :class:`LoanParams`, so already distinct); empty on a loan-free database.
    """
    rows = (
        db.session.query(LoanParams.account_id)
        .order_by(LoanParams.account_id)
        .all()
    )
    return [account_id for (account_id,) in rows]


def load_anchor_events(account_id: int) -> list:
    """Load every :class:`LoanAnchorEvent` for a loan account (unordered).

    The shared anchor-history loader for the loan consumers
    (:func:`app.services.loan_payment_service.resolve_account_loan`, the loan
    PITI resolver, and the Step-4
    :func:`app.services.loan_posting_service.compute_loan_payment_splits`); the
    resolver and the split walk both select the latest event from the returned
    list via :func:`app.services.loan_resolver.select_latest_anchor` (so the
    ordering is irrelevant here and not imposed).  Centralising the query keeps
    the consumers from drifting on how a loan's anchor history is read.

    Args:
        account_id: The loan account whose anchor events to load.

    Returns:
        The account's :class:`LoanAnchorEvent` rows (possibly empty -- the
        origination backfill guarantees at least one in production, but a
        direct-insert test fixture may have none).
    """
    return (
        db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account_id)
        .all()
    )


def load_active_escrow_components(account_id: int) -> list:
    """Load a loan account's CURRENTLY-active escrow components, ordered by name.

    The "what escrow does this loan carry TODAY" loader -- used by
    :func:`app.services.loan_payment_service.load_loan_context` (the resolver /
    projection path) and the escrow display / recurring-cash surfaces, so the
    monthly-escrow figure each feeds
    to :func:`app.services.escrow_calculator.calculate_monthly_escrow` is summed
    over the IDENTICAL currently-active set.  Removed components (``end_date``
    set) are excluded -- "currently active" is exactly ``end_date IS NULL`` under
    the effective-dated model.  For the escrow active on a PAST payment's date
    (the loan-payment split), load every version with
    :func:`load_all_escrow_components` and filter each date with
    :meth:`~app.models.loan_features.EscrowComponent.is_active_on`.

    Args:
        account_id: The loan account whose escrow components to load.

    Returns:
        The currently-active (``end_date IS NULL``)
        :class:`~app.models.loan_features.EscrowComponent` rows, ascending by
        name (the order is irrelevant to the order-independent monthly sum, but
        kept stable for display callers).
    """
    return (
        db.session.query(EscrowComponent)
        .filter(
            EscrowComponent.account_id == account_id,
            EscrowComponent.end_date.is_(None),
        )
        .order_by(EscrowComponent.name)
        .all()
    )


def load_all_escrow_components(account_id: int) -> list:
    """Load EVERY escrow component version for a loan (active AND removed).

    The loan-payment split
    (:func:`app.services.loan_posting_service.compute_loan_payment_splits`) needs
    the escrow in effect on each HISTORICAL payment's date, which may be a
    version since removed, so it loads the full effective-dated history here and
    filters each payment's date in memory with
    :meth:`~app.models.loan_features.EscrowComponent.is_active_on` -- one query
    for the whole walk rather than one per payment.  Unlike
    :func:`load_active_escrow_components` this does NOT filter by ``end_date``.

    Args:
        account_id: The loan account whose full escrow history to load.

    Returns:
        Every :class:`~app.models.loan_features.EscrowComponent` row for the
        account (active and removed), unordered -- the caller filters by date and
        the monthly sum is order-independent.
    """
    return (
        db.session.query(EscrowComponent)
        .filter(EscrowComponent.account_id == account_id)
        .all()
    )


def query_shadow_income(account_id: int, scenario_id: int):
    """Return the base query for shadow-income transactions on an account.

    Shadow income is the income-leg shadow of a transfer INTO the account:
    a payment received by a loan, or a contribution into an investment
    account.  It is identified by ``transfer_id IS NOT NULL`` plus the
    Income transaction type, excluding soft-deleted rows and the
    balance-excluded statuses (Credit, Cancelled, via the centralized
    ``balance_excluded_status_ids`` accessor).  Centralizing that predicate
    keeps the loan-payment history and the year-end contribution feeds from
    drifting on what counts as shadow income (MED-02): a one-sided change
    to the rule would otherwise desynchronize the two surfaces.

    ``status`` and ``pay_period`` are eager-loaded because both current
    consumers read ``txn.status`` / ``txn.pay_period`` downstream without an
    N+1.  Period scoping and ordering stay with the caller because they
    differ: the payment history covers every period and orders by period
    start; the year-end feeds filter to a specific set of period IDs.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The active budget scenario.

    Returns:
        A SQLAlchemy ``Query`` over ``Transaction`` filtered to the
        account's shadow income (status + pay_period eager-loaded), NOT yet
        executed -- callers chain ``.filter`` / ``.join`` / ``.order_by`` /
        ``.all`` as their surface requires.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        db.session.query(Transaction)
        .options(
            joinedload(Transaction.status),
            joinedload(Transaction.pay_period),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.is_deleted.is_(False),
            ~Transaction.status_id.in_(balance_excluded_status_ids()),
        )
    )
