"""
Shekel Budget App -- Account Resolvers

Shared helpers to deterministically pick the account used by the
budget grid and the analytics services for balance and reporting
queries.

* ``resolve_grid_account`` -- 4-step fallback chain used by the
  budget grid (every step refuses an AMORTIZING account -- ruling D4
  / step A1; a loan's balance is not a transaction sum, see
  :func:`is_cash_flow_account`):

    1. override_account_id  (query param -- future URL-based override)
    2. user_settings.default_grid_account_id  (if set and still active)
    3. First active checking account  (by sort_order, id)
    4. First active grid-eligible account  (by sort_order, id)
    5. None

* ``resolve_analytics_account`` -- 2-step fallback used by the
  calendar and spending analytics surfaces.  Its explicit branch
  applies the SAME amortizing-kind gate (plan step X-a1), so neither
  resolver can hand a loan to a surface that renders a cash-flow
  balance.  No user-settings or override layer; the caller has
  already resolved either an explicit account_id or wants the user's
  default checking account.

The grid path keeps its richer fallback because the grid is the
primary UI for transaction display; the analytics path's narrower
fallback matches its reporting use case where "no account
configured" should produce an empty report rather than synthesise
an analysis against an arbitrary savings account.
"""

from app import ref_cache
from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.services import account_service


def is_cash_flow_account(account: Account) -> bool:
    """Return True when a CASH-FLOW surface may render *account*.

    A cash-flow surface renders a balance beside the account's OWN transaction
    rows, so the two have to reconcile on screen.  This predicate gates the
    surfaces reached through the two resolvers in this module: the budget
    grid, the dashboard hero and pulse, and the analytics calendar's
    end-of-day line and month-end figure.

    **What such a surface READS is no longer one answer** (plan step X-g3b).
    The dashboard and the calendar read the seam's cash entries
    (``cash_balance_map`` / ``cash_balance_at`` / ``cash_daily_balance_series``
    -- a pure running sum from the anchor); the grid reads
    ``grid_balance_view``, which renders the MODELLED balance with the accrual
    and contribution rows that explain it (ruling R-W).  Both are cash-flow
    surfaces in the sense this gate cares about, and the gate itself is
    unchanged by that split: a LOAN is refused from all of them.  The remaining
    consumer, the cash detail page, carries its own wider gate
    (``routes.accounts._cash_page.cash_detail_wrong_type``, which also
    refuses appreciating and retirement / investment kinds), so it is
    not routed through here.  An AMORTIZING loan's balance is not
    a transaction sum -- payment transfers INTO the loan read as inflows,
    so the grid rendered the real Mortgage RISING by the full PITI every
    month (plan-of-record finding B-3, ruling D4: the grid refuses an
    amortizing account; step A1).  Branches on the ``has_amortization``
    boolean column, never a type-name string, matching
    :func:`app.services.account_projection.classify_account`.

    **One predicate, both resolvers, because it is one question.**  It was
    ``is_grid_account`` and gated the grid path alone, which read as a
    grid preference rather than the kind rule it is -- and the calendar
    reached the same producers through :func:`resolve_analytics_account`
    with no gate at all.  Measured on a dev clone 2026-07-25, before plan
    step X-a1 closed it: ``/analytics?view=month&account_id=3`` rendered
    the Mortgage at ``$178,103.41`` and ``account_id=8`` rendered the Van
    Loan at ``$531.94``, where the loans owed ``$177,277.97`` and
    ``$15,663.59`` -- finding B-3 itself, on the one cash-flow surface
    ruling D4's enumeration missed (finding N-38).

    Args:
        account: An :class:`Account` with its ``account_type``
            relationship reachable (lazy load is fine; the resolver
            operates inside a request session).

    Returns:
        True for every non-amortizing kind (an account with no loaded
        type row classifies PLAIN and stays eligible, matching the
        classifier's degenerate branch); False for a loan.
    """
    acct_type = account.account_type
    return acct_type is None or not acct_type.has_amortization


def list_grid_accounts(user_id: int) -> list[Account]:
    """Return the user's active grid-eligible accounts, picker-ordered.

    The option list behind the settings page's "Default Grid Account"
    picker.  Applies the same amortizing-kind exclusion as
    :func:`resolve_grid_account` (ruling D4 / step A1) so the picker can
    never offer an account the resolver would refuse, and keeps
    ``account_service.list_active_accounts``'s ``(sort_order, name)``
    ordering so the dropdown matches every other account picker.

    Args:
        user_id: ``auth.users.id`` of the owner whose accounts to list.

    Returns:
        The owner's active non-amortizing :class:`Account` rows, ordered
        by ``sort_order`` then ``name``.
    """
    return (
        account_service.active_accounts_query(user_id, amortizing=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def _first_active_checking_account(user_id) -> Account | None:
    """Return the user's canonical checking account, or ``None``.

    The single definition of "which account is this user's checking
    account": the first active account of the CHECKING type, ordered by
    ``sort_order`` then ``id``.  Both resolvers fall back to this so the
    grid and analytics surfaces always pick the same account for a user;
    a change to the selection rule (a new tiebreaker, a primary flag)
    lives here once.

    Args:
        user_id: The current user's id.

    Returns:
        The first active checking :class:`Account`, or ``None`` when the
        user has no active checking account.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return (
        db.session.query(Account)
        .filter_by(
            user_id=user_id,
            is_active=True,
            account_type_id=checking_type_id,
        )
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def resolve_grid_account(user_id, user_settings=None, override_account_id=None):
    """Return the Account to use for grid balance display.

    Every step applies the amortizing-kind gate (ruling D4 / step A1):
    an override or saved default naming a loan behaves exactly like one
    naming a missing or archived account -- it falls through to the next
    step -- and the any-type fallback skips loans in SQL.  The grid (and
    the dashboard hero, which shares this resolver) can therefore never
    land on an amortizing account.

    Args:
        user_id: The current user's id.
        user_settings: The user's UserSettings object (or None).
        override_account_id: Explicit account id from a query param.

    Returns:
        An Account instance, or None if no active grid-eligible
        accounts exist.
    """
    # 1. Override from query param.
    if override_account_id is not None:
        acct = db.session.get(Account, override_account_id)
        if (
            acct and acct.user_id == user_id and acct.is_active
            and is_cash_flow_account(acct)
        ):
            return acct

    # 2. User setting.
    if user_settings and user_settings.default_grid_account_id:
        acct = db.session.get(Account, user_settings.default_grid_account_id)
        if (
            acct and acct.user_id == user_id and acct.is_active
            and is_cash_flow_account(acct)
        ):
            return acct

    # 3. First active checking account.
    acct = _first_active_checking_account(user_id)
    if acct:
        return acct

    # 4. First active account of any grid-eligible type.
    return (
        account_service.active_accounts_query(user_id, amortizing=False)
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def resolve_analytics_account(
    user_id: int,
    account_id: int | None,
) -> Account | None:
    """Return the account to scope analytics queries to.

    Two-step fallback chain used by the calendar, spending-report, and
    spending-trend analytics services:

      1. If ``account_id`` is provided, verify it exists, belongs to
         ``user_id``, is still active, and is a cash-flow kind
         (:func:`is_cash_flow_account`).  Return the account on success
         or ``None`` on any failure (mismatched user, inactive, missing
         row, amortizing).  Returning ``None`` rather than silently
         falling through is deliberate -- an explicit ``account_id``
         that fails the ownership check is an IDOR signal, not a
         request to pick a different account, and one naming a LOAN is
         a request the surface cannot answer rather than a request to
         answer about some other account.
      2. Fall back to the user's first active checking account by
         ``sort_order, id``.  CHECKING-typed by construction, so the
         kind gate has nothing to add on this branch.

    **Why the kind gate is here and not in the fold (plan step X-a1,
    finding N-38).**  The calendar's balance line and month-end figure
    are the seam's CASH-FLOW view -- ``cash_daily_balance_series`` and
    ``cash_balance_at`` -- which answers the account's pure transaction
    running balance and consults no kind, deliberately: its day cells
    render the same transaction rows, so a kind-correct balance beside
    them would not reconcile.  Pointing that view at a loan does not
    produce a wrong-looking number, it produces a confident one
    (``$531.94`` for a Van Loan owing ``$15,663.59``, measured before
    this gate).  The answer is the one ruling R-E gave for the same
    class of leak on the write side -- refuse at the SOURCE -- so the
    producers stay TOTAL and kind-blind, with no balance surface able
    to ask them a question about a loan.  The kind-correct entry
    (``balance_at.balance_at``) is where a loan's balance comes from.

    Unlike :func:`resolve_grid_account`, this helper does NOT consult
    ``UserSettings.default_grid_account_id`` or accept an
    ``override_account_id`` -- analytics callers operate on either an
    explicit account or the user's canonical checking account, with no
    intermediate UI-state layer.  It also does not FALL THROUGH on a
    refused account the way the grid resolver does: an explicit
    ``account_id`` is a question about that account, and answering it
    with a different account's balance would be a wrong answer rather
    than a missing one.  The calendar turns this ``None`` into a 404
    (``CalendarAccountNotResolvableError``), matching the cash detail
    page, which already 404s a loan (``_cash_page.cash_detail_wrong_type``).

    Args:
        user_id: The current user's id.  Used for ownership check on
            the explicit branch and for the fallback query.
        account_id: Optional explicit account id.  ``None`` triggers
            the fallback to the first active checking account.

    Returns:
        The :class:`Account` instance the analytics service should
        scope its queries to, or ``None`` when no suitable account
        exists.
    """
    if account_id is not None:
        acct = db.session.get(Account, account_id)
        if (
            acct and acct.user_id == user_id and acct.is_active
            and is_cash_flow_account(acct)
        ):
            return acct
        return None

    return _first_active_checking_account(user_id)
