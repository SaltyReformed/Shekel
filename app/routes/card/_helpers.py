"""
Shekel Budget App -- Card route package: the card gate.

Which accounts this package's doors serve, in one leaf module every door
imports, so a door added later cannot be written without it -- the argument
:mod:`app.routes.accounts._cash_page` makes for the cash detail page's gate,
learned there when a fragment shipped without one.
"""

from flask import abort

from app.models.account import Account
from app.services.account_projection import is_revolving
from app.utils.auth_helpers import get_or_404


def load_card_or_404(account_id: int) -> Account:
    """Load the current user's revolving account *account_id*, or 404.

    Two refusals, one answer.  ``get_or_404`` resolves a missing or
    cross-owner id to ``None`` (the project's "404 for not-found and
    not-yours" rule), and an account of any other kind is refused the same
    way: a card door reached with a checking id is a forged or stale request
    -- the terms form renders only for a card (**R-CC25**) -- and it is the
    cash detail page's own disposition for a kind it does not serve
    (:func:`~app.routes.accounts._cash_page.load_cash_account_or_404`).  The
    kind is decided by :func:`~app.services.account_projection.is_revolving`,
    the ONE predicate every card feature gates on (CC-1), never by a type
    name.

    Args:
        account_id: The URL's account id.

    Returns:
        The :class:`~app.models.account.Account`, owned by the current user
        and revolving.
    """
    account = get_or_404(Account, account_id)
    if account is None or not is_revolving(account):
        abort(404)
    return account
