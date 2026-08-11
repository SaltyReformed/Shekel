"""
Shekel Budget App -- Which accounts the cash detail PAGE serves

The kind gate for :func:`app.routes.accounts.detail.cash_detail` and every
fragment of that page, in one leaf module both the page and its fragments can
import.

**It moved here because a fragment shipped without it** (plan step X-f2-b's
adversarial review).  The page and two of its three fragments went through
``_load_cash_account_or_404``; the Balance history fragment, added in a module
the page itself imports, could not reach it without an import cycle and so
guarded on ownership alone.  ``GET /accounts/<loan id>/balance-history`` then
rendered a Balance history card for an amortizing account -- against ruling D4
/ step A1 (a loan's balance is not a cash anchor, finding B-15) -- captioned
with copy about modelled growth that is false for a loan.

A gate that one member of a family can be written without is a gate the next
member will be written without too, so it stops being a private helper of the
module that happened to need it first.  This module imports no blueprint and
no sibling route module, so every fragment can depend on it and none of them
can create a cycle.
"""

from flask import abort

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.models.account import Account
from app.utils.auth_helpers import get_or_404


def cash_detail_wrong_type(account: Account) -> bool:
    """Return True when *account* is a kind the cash detail page does NOT serve.

    The merged cash detail page serves Checking, the ``has_interest`` types
    (HYSA / Money Market / CD / HSA), and plain cash types (Savings, Credit
    Card, plain custom).  It does NOT serve loans (``has_amortization``),
    physical assets (``has_appreciation``), or retirement / investment
    accounts (category RETIREMENT or INVESTMENT) -- those keep their own
    screens.  Resolves by boolean type flag and integer category id only,
    never a ref-table ``name`` string (the IDs-for-logic invariant).  An
    account with no ``account_type`` (degenerate / partially loaded) is
    served as a plain cash account, matching ``classify_account``'s
    None-is-PLAIN convention.
    """
    acct_type = account.account_type
    if acct_type is None:
        return False
    return bool(
        acct_type.has_amortization
        or acct_type.has_appreciation
        or acct_type.category_id in (
            ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
            ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT),
        )
    )


# ── Cash Detail (checking + interest + plain cash) ────────────────


def load_cash_account_or_404(account_id: int) -> Account:
    """Load a cash-detail-served account for the current user, or 404.

    The shared gate for :func:`~app.routes.accounts.detail.cash_detail`
    and every fragment of that page -- the band, the balance hero and the
    Balance history card: ``get_or_404``
    resolves cross-owner / non-existent accounts to ``None`` (the
    project's "404 for not-found and not-yours" rule), and
    :func:`cash_detail_wrong_type` 404s the kinds this page does not
    serve (loans, physical assets, retirement / investment).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)
    if cash_detail_wrong_type(account):
        abort(404)
    return account
