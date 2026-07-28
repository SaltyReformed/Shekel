"""
Shekel Budget App -- Net-Worth Account-Data Adapter.

The shared bridge between the :mod:`app.services.balance_at` seam's
per-account balance maps and the net-worth reduction: it pairs each
account's dense period map with its asset/liability flag, producing the one
``{account_id, balances, is_liability}`` shape the savings cockpit's
net-worth producer (``savings_dashboard_service._net_worth``) reduces over.
It was shared with the year-end summary until that package was deleted
(plan step F2), which is why it lives in its own module rather than inside
the cockpit's net-worth producer.  Its importers today are ``_net_worth``
(:func:`to_net_worth_account_data`), ``_types`` and ``_orchestrator``
(:func:`is_liability_account`), so inlining it into any one of them would break
the others.  (The sentence here counted two importers and named ``_projections``
as one of them until plan step X-t5; X-t1 moved that classifier read onto
:attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`
and deleted the import, and the count never covered ``_orchestrator``.)

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


def to_net_worth_account_data(
    accounts: list, balance_maps: dict,
) -> list[dict]:
    """Pair each account's seam balance map with its liability flag.

    The net-worth-account-data adapter the savings cockpit's
    ``build_account_net_worth_maps`` feeds to its per-period reduction
    (``_sum_composition_at_period``).  It takes ``balance_maps`` as INPUT
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
        account that has a map.  All three keys are read downstream:
        ``balances`` and ``is_liability`` by the per-period net-worth
        reduction, and ``account_id`` both by the sparkline producer (to
        reuse the maps) and by that same reduction (to look up the account's
        composition band).
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
