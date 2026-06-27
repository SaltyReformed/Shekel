"""
Shekel Budget App -- Net-Worth Account-Data Adapter.

The shared bridge between the :mod:`app.services.balance_at` seam's
per-account balance maps and the net-worth sum: it pairs each account's
dense period map with its asset/liability flag, producing the one
``{account_id, balances, is_liability}`` shape BOTH net-worth consumers --
the savings cockpit (``savings_dashboard_service._net_worth``) and the
year-end summary (``year_end_summary_service._net_worth``) -- feed to
:func:`app.services.net_worth_kernel.sum_net_worth_at_period`.

Lives in its own module, between the consumers and the engine cluster, for
two reasons:

* The asset-vs-liability rule is account metadata, not a balance, so it
  does NOT belong in the ``balance_at`` seam (whose contract is balances
  only).
* It takes the seam's ``balance_maps`` as INPUT -- the consumer calls
  :func:`app.services.balance_at.build_maps` and passes the result here --
  so this module never imports the seam, and the seam never imports it.
  The consumer orchestrates both, keeping the dependency graph acyclic.

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
    treated as a non-liability asset.  Both net-worth consumers -- the
    savings cockpit and the year-end summary -- classify through this one
    home, so an account can never count as an asset on one surface and a
    liability on another.

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


def to_net_worth_account_data(
    accounts: list, balance_maps: dict,
) -> list[dict]:
    """Pair each account's seam balance map with its liability flag.

    The shared net-worth-account-data adapter both net-worth consumers
    feed to :func:`app.services.net_worth_kernel.sum_net_worth_at_period`:
    the savings cockpit's ``build_account_net_worth_maps`` and the year-end
    summary's ``_build_account_data``.  It takes ``balance_maps`` as INPUT
    -- the consumer calls :func:`app.services.balance_at.build_maps` and
    passes the result here -- so this module stays independent of the seam
    (see the module docstring).  Accounts whose map is absent (no anchor
    period, omitted by the seam) are skipped, matching the prior
    per-consumer ``balances is None`` skip.

    Args:
        accounts: The accounts to assemble, in the desired output order.
        balance_maps: account_id -> dense period balance map from
            :func:`app.services.balance_at.build_maps`.

    Returns:
        A list of ``{account_id, balances, is_liability}`` dicts, one per
        account that has a map.  ``account_id`` lets the savings cockpit's
        sparkline producer reuse the maps; the net-worth reducers ignore it
        (the year-end section reads only ``balances`` / ``is_liability``).
    """
    result: list[dict] = []
    for account in accounts:
        balances = balance_maps.get(account.id)
        if balances is None:
            continue
        result.append({
            "account_id": account.id,
            "balances": balances,
            "is_liability": is_liability_account(account),
        })
    return result
