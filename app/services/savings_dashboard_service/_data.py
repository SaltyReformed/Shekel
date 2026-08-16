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
from app.services import cash_ledger, pay_period_service
from app.services.projection_inputs import (
    load_investment_params_for_accounts,
)
from app.services.savings_dashboard_service._types import (
    ArchivedAccount,
    _AccountParams,
    _DashboardCoreData,
)


def _load_dashboard_core_data(balance_ctx):
    """Load the accounts and periods for the dashboard, in *balance_ctx*'s pass.

    Per-account balances are produced by the
    :mod:`app.services.balance_at` seam, which loads its own scenario-scoped
    transactions, so this loader no longer pre-fetches a transaction set.

    **The owner is the PASS's, and there is no second way to say it** (plan step
    C2-f2d-1).  This took a ``user_id`` AND an optional context, and built a
    context from the id when none arrived -- so one call could scope its account
    and period queries to one owner while every seam read and the memoized pay
    calendar answered for another, with nothing comparing them.  Unreachable in
    tree (both external callers build the pass from the same id they pass), and
    widened out of existence rather than documented, for the reason
    ``retirement_projection._resolve_seed_balances`` widens its memo key.
    **Building the pass moved OUT** with the id: a loader that manufactures a
    read pass is the shape ledger row **P43** records, one layer down.

    Sharing the CONTEXT is still not sharing the LOADS -- each producer calls
    this function and re-runs these queries, which is finding **N-115**.

    Args:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext` -- its ``user_id``
            scopes every query here, and its scenario, clock and memos serve
            every producer downstream.  The budget dashboard's tracks section
            runs TWO savings producers back to back and hands both the same one,
            so each loan resolves once for the pair.

    Returns:
        A :class:`_DashboardCoreData` with active accounts (ordered for
        display), the balance context, all pay periods, and the current
        period.  **Not the owner's pay cadence** -- see that class's docstring
        for why a loader every narrow producer runs must not resolve a fact
        those producers may return before using.
    """
    user_id = balance_ctx.user_id
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    return _DashboardCoreData(
        accounts=accounts,
        balance_ctx=balance_ctx,
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
    an account always carries an assertion (E-19), so the reducer could fire
    only on a genuine ``$0.00`` and return ``$0.00`` -- vacuous, and the
    truthiness-on-money shape ruling R-CA deleted eight of.  The figure itself
    is now read from that assertion rather than from the cache column that
    mirrored it (plan step X-f1c3a).

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
            account=acct,
            last_anchor_balance=cash_ledger.resolve_anchor(acct).balance,
        )
        for acct in accounts
    ]
