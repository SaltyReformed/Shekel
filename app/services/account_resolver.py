"""
Shekel Budget App -- Grid Account Resolver

Shared helper to deterministically pick the account used by the budget
grid for balance calculations.  Fallback chain:

1. override_account_id  (query param -- future URL-based override)
2. user_settings.default_grid_account_id  (if set and still active)
3. First active checking account  (by sort_order, id)
4. First active account of any type  (by sort_order, id)
5. None
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
