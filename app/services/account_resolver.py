"""
Shekel Budget App -- Account Resolvers

Shared helpers to deterministically pick the account used by the
budget grid and the analytics services for balance and reporting
queries.

* ``resolve_grid_account`` -- 4-step fallback chain used by the
  budget grid:

    1. override_account_id  (query param -- future URL-based override)
    2. user_settings.default_grid_account_id  (if set and still active)
    3. First active checking account  (by sort_order, id)
    4. First active account of any type  (by sort_order, id)
    5. None

* ``resolve_analytics_account`` -- 2-step fallback used by the
  analytics services (budget_variance, calendar, spending_trend).
  No user-settings or override layer; the caller has already
  resolved either an explicit account_id or wants the user's
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


def resolve_grid_account(user_id, user_settings=None, override_account_id=None):
    """Return the Account to use for grid balance display.

    Args:
        user_id: The current user's id.
        user_settings: The user's UserSettings object (or None).
        override_account_id: Explicit account id from a query param.

    Returns:
        An Account instance, or None if no active accounts exist.
    """
    # 1. Override from query param.
    if override_account_id is not None:
        acct = db.session.get(Account, override_account_id)
        if acct and acct.user_id == user_id and acct.is_active:
            return acct

    # 2. User setting.
    if user_settings and user_settings.default_grid_account_id:
        acct = db.session.get(Account, user_settings.default_grid_account_id)
        if acct and acct.user_id == user_id and acct.is_active:
            return acct

    # 3. First active checking account.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    acct = (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True, account_type_id=checking_type_id)
        .order_by(Account.sort_order, Account.id)
        .first()
    )
    if acct:
        return acct

    # 4. First active account of any type.
    return (
        db.session.query(Account)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def resolve_analytics_account(
    user_id: int,
    account_id: int | None,
) -> Account | None:
    """Return the account to scope analytics queries to.

    Two-step fallback chain used by the budget-variance, calendar,
    spending-trend, and similar analytics services:

      1. If ``account_id`` is provided, verify it exists, belongs to
         ``user_id``, and is still active.  Return the account on
         success or ``None`` on any failure (mismatched user, inactive,
         missing row).  Returning ``None`` rather than silently falling
         through is deliberate -- an explicit ``account_id`` that fails
         the ownership check is an IDOR signal, not a request to pick
         a different account.
      2. Fall back to the user's first active checking account by
         ``sort_order, id``.

    Unlike :func:`resolve_grid_account`, this helper does NOT consult
    ``UserSettings.default_grid_account_id`` or accept an
    ``override_account_id`` -- analytics callers operate on either an
    explicit account or the user's canonical checking account, with no
    intermediate UI-state layer.

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
        if acct and acct.user_id == user_id and acct.is_active:
            return acct
        return None

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
