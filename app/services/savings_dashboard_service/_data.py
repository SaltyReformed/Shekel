"""
Shekel Budget App -- Savings Dashboard: batch data loaders.

Loads the request-scoped core data (accounts, scenario, periods), the
account-type-specific parameter maps that drive the projection loop, and
the archived-account list.  No Flask imports; every function takes plain
data and returns plain data.
"""

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.account import Account
from app.models.escrow_line import EscrowLine
from app.models.interest_params import InterestParams
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.services import pay_period_service
from app.services.projection_inputs import (
    load_investment_params_for_accounts,
)
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service._types import (
    ArchivedAccount,
    _AccountParams,
    _DashboardCoreData,
)


def _load_dashboard_core_data(user_id, balance_ctx=None):
    """Load the accounts, balance context, and periods for the dashboard.

    Per-account balances are produced by the
    :mod:`app.services.balance_at` seam, which loads its own scenario-scoped
    transactions, so this loader no longer pre-fetches a transaction set.

    It builds the read pass's
    :class:`~app.services.balance_at.BalanceContext` (resolving the
    baseline scenario once) unless the caller supplies one.  Every producer in
    the build then shares it, so each loan is resolved exactly once for the whole
    render rather than once per surface that asks.

    Args:
        user_id: Integer ID of the current user.
        balance_ctx: An existing
            :class:`~app.services.balance_at.BalanceContext` to reuse, or
            ``None`` to build one for this pass.  A caller that has already
            started a read pass (the budget dashboard's tracks section runs TWO
            savings producers back to back -- it ran three until plan step X-u
            deleted the second debt one) passes its own so all of them share
            ONE set of loan resolutions.  Sharing the CONTEXT is not sharing the
            LOADS: each producer still calls this function, which is finding
            N-115.

    Returns:
        A :class:`_DashboardCoreData` with active accounts (ordered for
        display), the balance context, all pay periods, and the current
        period.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    return _DashboardCoreData(
        accounts=accounts,
        balance_ctx=(
            balance_ctx if balance_ctx is not None
            else BalanceContext.build(user_id)
        ),
        all_periods=pay_period_service.get_all_periods(user_id),
        current_period=pay_period_service.get_current_period(user_id),
    )


def _load_loan_params_and_escrow(accounts):
    """Batch-load LoanParams and EscrowLine maps for loan accounts.

    Amortizing loan types are metadata-driven via ``has_amortization``.

    Args:
        accounts: List of Account model instances.

    Returns:
        ``(loan_params_map, escrow_map)`` -- the first maps account_id
        to its :class:`LoanParams`; the second maps account_id to a
        list of :class:`~app.models.escrow_line.EscrowLine` with their
        versions (for the debt-summary PITI total, resolved to today by
        :func:`~app.services.escrow_calculator.escrow_monthly_as_of`).  Both
        are empty when no loan accounts exist.
    """
    amort_type_ids = {
        at.id for at in db.session.query(AccountType).filter_by(has_amortization=True).all()
    }
    loan_account_ids = [a.id for a in accounts if a.account_type_id in amort_type_ids]

    loan_params_map = {}
    escrow_map = {}
    if loan_account_ids:
        for lp in db.session.query(LoanParams).filter(
            LoanParams.account_id.in_(loan_account_ids)
        ).all():
            loan_params_map[lp.account_id] = lp

        # Escrow LINES (with their versions) for the loan accounts, batched to
        # avoid an N+1 across loans; the metric resolves each to today's active
        # version via ``escrow_monthly_as_of``.  The whole line set is loaded (no
        # active pre-filter): "active on today" is a per-line supersession
        # resolution the calculator owns, not a stored flag to filter on.
        for line in db.session.query(EscrowLine).options(
            selectinload(EscrowLine.versions),
        ).filter(
            EscrowLine.account_id.in_(loan_account_ids),
        ).all():
            escrow_map.setdefault(line.account_id, []).append(line)

    return loan_params_map, escrow_map


def _load_account_params(accounts: list[Account]) -> _AccountParams:
    """Batch-load all account-type-specific parameters.

    Returns an :class:`_AccountParams` with the four account-type parameter
    maps (each keyed by ``account_id``) the projection loop reads.  This is
    the single place all four are constructed.

    The deductions and engine-gross inputs the growth projection needs are
    NOT loaded here: each per-account tile delegates its projection to the
    :mod:`app.services.balance_at` seam, which assembles those itself from the
    shared loaders, so loading them here was a dead per-request deductions
    query + paycheck-engine call no consumer read.
    """
    interest_params_map = {}
    interest_account_ids = [
        a.id for a in accounts
        if a.account_type and a.account_type.has_interest
    ]
    if interest_account_ids:
        for hp in db.session.query(InterestParams).filter(
            InterestParams.account_id.in_(interest_account_ids)
        ).all():
            interest_params_map[hp.account_id] = hp

    # Investment/retirement accounts use the growth engine.  The shared
    # loader owns the canonical-classifier filter + InvestmentParams query
    # (its single home, shared with the balance_at seam), so a parameterised
    # physical asset (Property -> APPRECIATING) is correctly excluded there
    # rather than re-derived "by elimination".
    investment_params_map = load_investment_params_for_accounts(accounts)

    loan_params_map, escrow_map = _load_loan_params_and_escrow(accounts)

    return _AccountParams(
        interest_params_map=interest_params_map,
        investment_params_map=investment_params_map,
        loan_params_map=loan_params_map,
        escrow_map=escrow_map,
    )


def _load_archived_accounts(user_id: int) -> list[ArchivedAccount]:
    """Load archived accounts with minimal data for the collapsed section.

    Archived accounts do not receive balance projections, engine calls,
    or goal calculations -- they are historical, so the only figure the drawer
    can show is the last balance the user asserted for the account.

    **That figure is NAMED for what it is** (plan step X-w2, ruling R-CH,
    finding N-114).  The rows were untyped ``{account, current_balance}`` dicts,
    and ``current_balance`` is what
    :class:`~.._types.AccountProjection` calls the seam-derived balance every
    LIVE tile renders -- a different fact under the same key, on the same page.
    :class:`~.._types.ArchivedAccount` carries why that matters and which
    finding owns the question of whether the line belongs here at all.

    The ``or Decimal("0.00")`` this loop used to apply is gone with the dict:
    ``accounts.current_anchor_balance`` is ``NOT NULL`` with a redundant CHECK
    beside it, so the reducer could fire only on a genuine ``$0.00`` and return
    ``$0.00`` -- vacuous, and the truthiness-on-money shape ruling R-CA deleted
    eight of.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        The :class:`~.._types.ArchivedAccount` rows, ordered for display.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return [
        ArchivedAccount(
            account=acct, last_anchor_balance=acct.current_anchor_balance,
        )
        for acct in accounts
    ]
