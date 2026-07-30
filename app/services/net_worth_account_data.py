"""
Shekel Budget App -- the asset-vs-liability classifier.

The one home for the rule every net-worth surface classifies through: is this
account's type in the LIABILITY category?  The savings cockpit's hero, its
per-period composition bands, its Horizon liability band and its grid cells all
reach it through
:attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`,
which IS this function, so an account can never count as an asset on one surface
and a liability on another.

**It was an ADAPTER as well until plan step X-w** (ruling R-CG, finding N-114).
``to_net_worth_account_data`` paired each account's dense period map with a
STORED copy of this flag, producing a second per-account container beside the
projection that derives it -- one rule single-sourced in one container and not
in the other.  The dense map now rides on the projection itself, so the adapter
had nothing left to adapt and the classifier is what remains.

It lives in its own module rather than inside the cockpit package because its
importers are in two of them: ``savings_dashboard_service._types`` (the
projection's property) and ``savings_dashboard_service._orchestrator`` (the
narrow debt producer's liability filter).  It was shared with the year-end
summary until that package was deleted (plan step F2).  The rule does NOT belong
in the ``balance_at`` seam either: it is account metadata, not a balance, and the
seam's contract is balances only.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no
Flask import, no database writes.  Liability classification uses the
cached reference-table id (IDs for logic, never a ``.name`` string).
"""

from app import ref_cache
from app.enums import AcctCategoryEnum


def is_liability_account(account) -> bool:
    """Return whether an account's type is in the LIABILITY category.

    The canonical asset-vs-liability classifier the net-worth sum depends
    on: it compares the account type's integer ``category_id`` against the
    cached LIABILITY category id (IDs for logic, never a ``.name`` string).
    An account with no ``account_type`` (degenerate / partially loaded) is
    treated as a non-liability asset.  Every net-worth surface classifies
    through this one home -- the cockpit's today figures, its trend, and its
    Horizon band -- so an account can never count as an asset on one surface
    and a liability on another.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        ``True`` when the account's type's category is LIABILITY,
        ``False`` otherwise.
    """
    liability_cat_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    return (
        account.account_type is not None
        and account.account_type.category_id == liability_cat_id
    )
