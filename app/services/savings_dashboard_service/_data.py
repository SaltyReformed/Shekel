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
from app.services import cash_ledger
from app.services.account_category import is_liability_account
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from app.services.projection_inputs import (
    load_investment_params_for_accounts,
)
from app.services.savings_dashboard_service._tile import tile_balance_on
from app.services.savings_dashboard_service._types import (
    ArchivedAccount,
    ArchivedDebt,
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
        display) and the balance context -- and NOTHING derived from the
        latter.  **Not the owner's pay cadence, not the period SET, and not the
        current period**: every one of those is a fact a narrow producer may
        return before using, and a loader every narrow producer runs must not
        resolve one.  That rule was learned by the cadence at plan step
        R7a-2a and re-learned by the current period at C2-f2d-3, both on this
        function; see :class:`_DashboardCoreData`.
    """
    user_id = balance_ctx.user_id
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    # NEITHER period question is resolved here (plan step C2-f2d-3): the SET is
    # ``balance_ctx.reported_periods()``, the domain the seam already reports
    # over, and the current period is a PROPERTY on the bundle -- see that
    # class for the legacy owner this loader raised for when it derived one
    # eagerly for two producers that return before reading it.
    return _DashboardCoreData(accounts=accounts, balance_ctx=balance_ctx)


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


def _load_archived_accounts(
    ctx: BalanceContext,
) -> list[ArchivedAccount | ArchivedDebt]:
    """Load archived accounts with minimal data for the collapsed section.

    Archived accounts do not receive balance projections, engine calls,
    or goal calculations -- they are historical.  A non-debt's drawer figure is
    the last balance the user asserted for it; a DEBT's is what it owes today
    (ruling **R-CC67**, with R-CC53 for a loan; ledger row CC-362, plan step
    credit_card:CC-5-5c).

    **That figure is NAMED for what it is** (plan step X-w2, ruling R-CH,
    finding N-114).  The rows were untyped ``{account, current_balance}`` dicts,
    and ``current_balance`` is what
    :class:`~.._types.AccountProjection` calls the seam-derived balance every
    LIVE tile renders -- a different fact under the same key, on the same page.
    :class:`~.._types.ArchivedAccount` carries why that matters.  A debt is an
    :class:`~.._types.ArchivedDebt` whose one figure is ``owed``: what the
    debt's TILE would show at the pass's day
    (:func:`.._tile.tile_balance_on`) through
    :func:`app.services.liability_sign.owed`, the figure a live liability
    tile shows (:attr:`~.._types.AccountProjection.shown_balance`).  It read the
    debt's typed assertion until CC-5-5c, which for a configured loan is not
    its balance in either sign: an archived Mortgage on the 2026-09-22 17:06
    production dump read ``$178,103.41`` where it owed ``$176,719.77``.

    **The tile's rule, not the day, since plan step CC-5-5d** (ledger row
    **CC-371**, ruling R-CC88).  CC-5-5c read the seam at the pass's day for
    every debt, which is the tile's rule for a configured loan only: a card's
    tile reads its column for the current pay period -- the balance at the
    period's END -- so an archived card with a purchase planned later in the
    period showed less owed than its live tile had (measured: tile
    ``-1,200.00``, the day ``-1,000.00``), where ruling R-CC67 says "the same
    calculation as a live /savings tile".

    The ``or Decimal("0.00")`` this loop used to apply is gone with the dict:
    an account always carries an assertion (E-19), so the reducer could fire
    only on a genuine ``$0.00`` and return ``$0.00`` -- vacuous, and the
    truthiness-on-money shape ruling R-CA deleted eight of.  The figure itself
    is now read from that assertion rather than from the cache column that
    mirrored it (plan step X-f1c3a).

    Args:
        ctx: The page's read pass: its ``user_id`` scopes the query, and a
            debt is valued by the tile's rule at its ``as_of`` -- the same
            reading every live tile on the page makes.

    Returns:
        One row per archived account, ordered for display:
        :class:`~.._types.ArchivedDebt` for a liability,
        :class:`~.._types.ArchivedAccount` for everything else.
    """
    accounts = (
        db.session.query(Account)
        .filter_by(user_id=ctx.user_id, is_active=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return [
        ArchivedDebt(
            account=acct,
            owed=owed(tile_balance_on(acct, ctx, ctx.as_of)),
        )
        if is_liability_account(acct)
        else ArchivedAccount(
            account=acct,
            last_anchor_balance=cash_ledger.resolve_anchor(acct).balance,
        )
        for acct in accounts
    ]
